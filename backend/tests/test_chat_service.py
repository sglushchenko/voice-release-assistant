from datetime import date, timedelta

import httpx
import pytest

from release_assistant.agent.errors import TokenBudgetExceededError
from release_assistant.agent.scripted import ScriptedModel, say
from release_assistant.agent.service import ChatService
from release_assistant.domain.errors import NotFoundError
from test_api_chat import make_chat_app


def service(model: ScriptedModel, **kwargs: object) -> tuple[ChatService, list[str]]:
    app = make_chat_app(model)
    expired = bool(kwargs.pop("expired", False))
    touched: list[str] = []

    def client_for(owner: str) -> httpx.AsyncClient:
        token = app.state.auth.agent_credential(owner)
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://agent",
            headers={"Authorization": f"Bearer {token}"},
        )

    def touch(session_id: str, owner: str) -> None:
        touched.append(session_id)
        if expired:
            raise NotFoundError("gone")

    return ChatService(model, client_for, touch, **kwargs), touched  # type: ignore[arg-type]


async def test_the_oldest_conversation_is_dropped_beyond_the_limit() -> None:
    chat, _ = service(ScriptedModel([say("a"), say("b"), say("c")]), max_conversations=2)

    first = await chat.send("alice", "1")
    await chat.send("alice", "2")
    await chat.send("alice", "3")

    with pytest.raises(NotFoundError):
        await chat.send("alice", "again", first.conversation_id)


async def test_a_conversation_whose_session_expired_cannot_continue() -> None:
    chat, _ = service(ScriptedModel([say("a"), say("b")]), expired=True)
    first = await chat.send("alice", "1")

    with pytest.raises(NotFoundError, match="expired"):
        await chat.send("alice", "2", first.conversation_id)
    with pytest.raises(NotFoundError):  # and it is forgotten, not retried
        await chat.send("alice", "3", first.conversation_id)


async def test_continuing_keeps_the_api_session_alive() -> None:
    chat, touched = service(ScriptedModel([say("a"), say("b")]))
    first = await chat.send("alice", "1")

    await chat.send("alice", "2", first.conversation_id)

    assert len(touched) == 1


async def test_the_budget_resets_on_the_next_day() -> None:
    today = [date(2026, 11, 20)]
    chat, _ = service(
        ScriptedModel([say("a"), say("b"), say("c")]),
        daily_token_budget=100,
        today=lambda: today[0],
    )
    await chat.send("alice", "1")  # costs 120 tokens, over the allowance

    with pytest.raises(TokenBudgetExceededError):
        await chat.send("alice", "2")
    today[0] += timedelta(days=1)

    assert (await chat.send("alice", "3")).run.answer == "b"  # "2" never reached the model


async def test_budgets_are_kept_per_account() -> None:
    chat, _ = service(ScriptedModel([say("a"), say("b")]), daily_token_budget=100)
    await chat.send("alice", "1")

    assert (await chat.send("bob", "1")).run.answer == "b"
