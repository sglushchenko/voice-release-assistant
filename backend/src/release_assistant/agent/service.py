"""The chat service: many people's conversations with the agent, held by the server.

One conversation is one API session (where the server keeps its trust state, ADR 0003) plus a
stored history. Ids come from the server and are bound to the account, so a client can neither
invent a conversation nor read someone else's. Everything is in memory, like the sessions it
depends on: a restart ends every conversation, and a client simply starts a new one.
"""

import asyncio
import secrets
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import httpx
import structlog

from release_assistant.agent.chat import Chat
from release_assistant.agent.conversation import ConversationStore, InMemoryConversationStore
from release_assistant.agent.errors import (
    ChatUnavailableError,
    TokenBudgetExceededError,
    ToolBackendError,
)
from release_assistant.agent.http_backend import HttpToolBackend
from release_assistant.agent.messages import AgentRun
from release_assistant.agent.ports import LLMClient
from release_assistant.agent.runner import Agent
from release_assistant.domain.errors import NotFoundError

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ChatReply:
    conversation_id: str
    run: AgentRun


@dataclass
class _Conversation:
    chat: Chat
    session_id: str
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _utc_today() -> date:
    return datetime.now(UTC).date()


class ChatService:
    """
    Args:
        model: The language model, or None when none is configured (chat then refuses).
        client_for: An HTTP client for the API holding the agent's credential for an owner.
            Kept as a callable so the agent stays an ordinary API client (ADR 0007).
        touch_session: Keeps an API session alive for its owner and raises NotFoundError once
            it has expired; a conversation whose session is gone cannot continue, because its
            trust state went with it.
        store: Where histories are kept.
        daily_token_budget: Tokens one account may spend per UTC day.
        max_conversations: Live conversations; the least recently used is dropped beyond this.
        today: Injected so tests control the day.
    """

    def __init__(
        self,
        model: LLMClient | None,
        client_for: Callable[[str], httpx.AsyncClient],
        touch_session: Callable[[str, str], object],
        *,
        store: ConversationStore | None = None,
        daily_token_budget: int = 500_000,
        max_conversations: int = 100,
        today: Callable[[], date] = _utc_today,
    ) -> None:
        self._model = model
        self._client_for = client_for
        self._touch_session = touch_session
        self._store = store or InMemoryConversationStore()
        self._budget = daily_token_budget
        self._max = max_conversations
        self._today = today
        self._live: OrderedDict[tuple[str, str], _Conversation] = OrderedDict()
        self._spent: dict[str, tuple[date, int]] = {}
        self._clients: dict[str, httpx.AsyncClient] = {}

    @property
    def enabled(self) -> bool:
        return self._model is not None

    async def send(self, owner: str, text: str, conversation_id: str | None = None) -> ChatReply:
        """Answer one message, in a new conversation or the owner's existing one.

        Raises:
            ChatUnavailableError: No model is configured, or the tools could not be set up.
            TokenBudgetExceededError: The account used up today's allowance.
            NotFoundError: The conversation is unknown, expired or someone else's.
        """
        model = self._model
        if model is None:
            raise ChatUnavailableError("Chat is not configured: the server has no language model")
        if self._used_today(owner) >= self._budget:
            raise TokenBudgetExceededError("The daily allowance of model tokens is used up")

        conversation_id, conversation = (
            await self._open(owner, model)
            if conversation_id is None
            else (conversation_id, self._existing(owner, conversation_id))
        )
        async with conversation.lock:
            run = await conversation.chat.send(text)
        self._spend(owner, run.usage.total)
        return ChatReply(conversation_id, run)

    async def _open(self, owner: str, model: LLMClient) -> tuple[str, _Conversation]:
        client = self._clients.get(owner)
        if client is None:
            client = self._clients[owner] = self._client_for(owner)
        try:
            backend = await HttpToolBackend.open(client)
        except ToolBackendError as error:
            raise ChatUnavailableError("The assistant's tools are unavailable right now") from error
        conversation_id = secrets.token_urlsafe(16)
        conversation = _Conversation(
            Chat(Agent(model, backend), self._store, self._key(owner, conversation_id)),
            backend.session_id,
        )
        self._live[(owner, conversation_id)] = conversation
        while len(self._live) > self._max:
            (old_owner, old_id), _ = self._live.popitem(last=False)
            await self._store.delete(self._key(old_owner, old_id))
        return conversation_id, conversation

    def _existing(self, owner: str, conversation_id: str) -> _Conversation:
        conversation = self._live.get((owner, conversation_id))
        if conversation is None:
            raise NotFoundError("Conversation not found or expired; start a new one")
        try:
            self._touch_session(conversation.session_id, owner)
        except NotFoundError:
            del self._live[(owner, conversation_id)]
            raise NotFoundError("Conversation not found or expired; start a new one") from None
        self._live.move_to_end((owner, conversation_id))
        return conversation

    @staticmethod
    def _key(owner: str, conversation_id: str) -> str:
        return f"{owner}\x1f{conversation_id}"

    def _used_today(self, owner: str) -> int:
        day, tokens = self._spent.get(owner, (self._today(), 0))
        return tokens if day == self._today() else 0

    def _spend(self, owner: str, tokens: int) -> None:
        self._spent[owner] = (self._today(), self._used_today(owner) + tokens)
        log.info("chat.tokens_spent", tokens=tokens, today=self._spent[owner][1])
