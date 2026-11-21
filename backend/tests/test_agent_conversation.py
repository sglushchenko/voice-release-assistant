"""Conversation storage, trimming, restored trust, and the Chat facade."""

import hashlib

import httpx

from release_assistant.agent.chat import Chat
from release_assistant.agent.conversation import InMemoryConversationStore, conversation_is_tainted
from release_assistant.agent.http_backend import HttpToolBackend
from release_assistant.agent.messages import Message, Role, ToolCall, ToolResult, Trust
from release_assistant.agent.runner import Agent
from release_assistant.agent.scripted import ScriptedModel, say, use
from release_assistant.api.app import create_app
from release_assistant.bootstrap import build_mock_application
from release_assistant.config import ApiClient, Settings


def exchange(n: int, *, with_tool: bool = False) -> list[Message]:
    """One user turn, optionally with a tool round trip, ending in an answer."""
    messages = [Message.user(f"question {n}")]
    if with_tool:
        call = ToolCall(id=f"c{n}", name="list_apps", arguments={})
        result = ToolResult(call_id=f"c{n}", name="list_apps", content="{}")
        messages += [Message.assistant("", (call,)), Message.tool(result, "{}")]
    return [*messages, Message.assistant(f"answer {n}")]


def tool_message(trust: Trust) -> Message:
    result = ToolResult(call_id="1", name="read_reviews", content="text", trust=trust)
    return Message.tool(result, "text")


# --- the store -------------------------------------------------------------------------


async def test_conversations_are_kept_apart_by_id() -> None:
    store = InMemoryConversationStore()
    await store.append("a", exchange(1))
    await store.append("b", exchange(2))

    assert [m.content for m in await store.load("a")] == ["question 1", "answer 1"]
    assert [m.content for m in await store.load("b")] == ["question 2", "answer 2"]
    assert await store.load("unknown") == []


async def test_loading_returns_a_copy() -> None:
    store = InMemoryConversationStore()
    await store.append("a", exchange(1))

    (await store.load("a")).clear()

    assert len(await store.load("a")) == 2


async def test_trimming_never_leaves_a_tool_result_without_its_call() -> None:
    store = InMemoryConversationStore(max_messages=8)
    for n in range(1, 8):
        await store.append("a", exchange(n, with_tool=True))

    history = await store.load("a")

    assert history[0].role is Role.USER
    assert history[-1].content == "answer 7"
    assert len(history) <= 8 + 4  # a whole exchange is never split
    for index, message in enumerate(history):
        if message.role is Role.TOOL:
            assert history[index - 1].tool_calls, "a tool message must follow its call"


async def test_a_short_conversation_is_not_trimmed() -> None:
    store = InMemoryConversationStore(max_messages=50)
    for n in range(3):
        await store.append("a", exchange(n))

    assert len(await store.load("a")) == 6


# --- trust that survives a restore -----------------------------------------------------


def test_a_conversation_that_read_reviews_is_tainted() -> None:
    assert conversation_is_tainted([*exchange(1), tool_message(Trust.UNTRUSTED)]) is True


def test_trusted_or_empty_conversations_are_not_tainted() -> None:
    assert conversation_is_tainted([*exchange(1, with_tool=True)]) is False
    assert conversation_is_tainted([]) is False


async def test_trust_can_be_rebuilt_from_stored_history() -> None:
    """The point of deriving trust from messages: a resumed chat is tainted again without any
    separate flag having to be persisted."""
    app = create_app(
        Settings(
            _env_file=None,
            api_clients=[
                ApiClient(
                    subject="agent",
                    owner="alice",
                    key_sha256=hashlib.sha256(b"k").hexdigest(),
                    scopes=frozenset({"read", "propose"}),
                )
            ],
        ),
        build_mock_application(),
    )
    store = InMemoryConversationStore()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://api",
        headers={"Authorization": "Bearer k"},
    ) as client:
        backend = await HttpToolBackend.open(client)
        model = ScriptedModel([use("read_reviews", app_id="app-1"), say("Summarised.")])
        await Chat(Agent(model, backend), store, "chat-1").send("any complaints?")

    assert conversation_is_tainted(await store.load("chat-1")) is True


# --- the chat facade -------------------------------------------------------------------


async def test_a_chat_carries_the_history_into_the_next_message() -> None:
    class NoTools:
        async def call(self, call: ToolCall) -> ToolResult:  # pragma: no cover
            raise AssertionError("no tool should be called")

    model = ScriptedModel([say("Hello Anna."), say("You said your name is Anna.")])
    chat = Chat(Agent(model, NoTools()), InMemoryConversationStore(), "chat-1")

    await chat.send("I am Anna")
    await chat.send("who am I?")

    second_call = model.seen[1]
    contents = [m.content for m in second_call if m.role is not Role.SYSTEM]
    assert contents == ["I am Anna", "Hello Anna.", "who am I?"]


async def test_two_chats_do_not_share_history() -> None:
    class NoTools:
        async def call(self, call: ToolCall) -> ToolResult:  # pragma: no cover
            raise AssertionError("no tool should be called")

    store = InMemoryConversationStore()
    model = ScriptedModel([say("one"), say("two")])
    await Chat(Agent(model, NoTools()), store, "a").send("first")
    await Chat(Agent(model, NoTools()), store, "b").send("second")

    assert [m.content for m in model.seen[1] if m.role is not Role.SYSTEM] == ["second"]
