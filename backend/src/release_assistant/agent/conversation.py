"""Conversation history: where it is kept, how it is trimmed, and what it says about trust."""

from collections.abc import Sequence
from typing import Protocol

from release_assistant.agent.messages import Message, Role, Trust


class ConversationStore(Protocol):
    """Persistence for conversations, keyed by an id.

    In memory now. A database implementation, or one that lists a user's chats, plugs in here
    without touching the agent: a stored conversation is just messages, so resuming one means
    loading it and calling the agent again.
    """

    async def load(self, conversation_id: str) -> list[Message]: ...

    async def append(self, conversation_id: str, messages: Sequence[Message]) -> None: ...


def conversation_is_tainted(messages: Sequence[Message]) -> bool:
    """Whether any tool result in the conversation was untrusted third-party text.

    Trust is derived from the messages themselves, so it survives a restore: a resumed
    conversation that once read customer reviews is treated as untrusted again.
    """
    return any(
        m.tool_result is not None and m.tool_result.trust is Trust.UNTRUSTED for m in messages
    )


class InMemoryConversationStore:
    """History in process memory, trimmed to the most recent exchanges.

    Args:
        max_messages: Soft cap on a conversation's length. Trimming starts at a user message so
            a tool result is never left without the call that produced it.
    """

    def __init__(self, max_messages: int = 60) -> None:
        self._max_messages = max_messages
        self._conversations: dict[str, list[Message]] = {}

    async def load(self, conversation_id: str) -> list[Message]:
        return list(self._conversations.get(conversation_id, []))

    async def append(self, conversation_id: str, messages: Sequence[Message]) -> None:
        history = self._conversations.setdefault(conversation_id, [])
        history.extend(messages)
        self._conversations[conversation_id] = self._trim(history)

    def _trim(self, history: list[Message]) -> list[Message]:
        if len(history) <= self._max_messages:
            return history
        user_positions = [i for i, m in enumerate(history) if m.role is Role.USER]
        if not user_positions:
            return history
        wanted = len(history) - self._max_messages
        start = next((i for i in user_positions if i >= wanted), user_positions[-1])
        return history[start:]
