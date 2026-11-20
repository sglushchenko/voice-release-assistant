"""The chat endpoint: one message in, an answer and the server's pending actions out."""

import hashlib
from collections.abc import Sequence

import httpx
import pytest
from fastapi import FastAPI

from release_assistant.agent.messages import ModelTurn, Usage
from release_assistant.agent.scripted import ScriptedModel, say, use
from release_assistant.api.app import create_app
from release_assistant.bootstrap import build_mock_application
from release_assistant.config import ApiClient, Scope, Settings

KEY = "ios-key"
OTHER_KEY = "bob-key"
NO_CHAT_KEY = "readonly-key"


def client(subject: str, owner: str, key: str, *scopes: Scope) -> ApiClient:
    return ApiClient(
        subject=subject,
        owner=owner,
        key_sha256=hashlib.sha256(key.encode()).hexdigest(),
        scopes=frozenset(scopes),
    )


def make_chat_app(model: ScriptedModel | None, **settings: object) -> FastAPI:
    config = Settings(
        _env_file=None,
        api_clients=[
            client("ios", "alice", KEY, "read", "chat", "confirm:button"),
            client("web", "bob", OTHER_KEY, "read", "chat"),
            client("viewer", "alice", NO_CHAT_KEY, "read"),
        ],
        **settings,  # type: ignore[arg-type]
    )
    return create_app(config, build_mock_application(), chat_model=model)


def talk(app: FastAPI, key: str = KEY) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://api",
        headers={"Authorization": f"Bearer {key}"},
    )


async def test_a_message_is_answered_and_the_cost_is_reported() -> None:
    app = make_chat_app(ScriptedModel([use("list_apps"), say("You have two apps.")]))

    async with talk(app) as api:
        response = await api.post("/v1/chat", json={"message": "which apps do I have?"})

    body = response.json()
    assert response.status_code == 200
    assert body["answer"] == "You have two apps."
    assert (body["stop_reason"], body["steps"], body["tool_calls"]) == ("answered", 2, 1)
    assert body["usage"] == {"input_tokens": 200, "output_tokens": 40}
    assert body["pending_actions"] == []
    assert body["conversation_id"]


async def test_the_conversation_continues_with_its_history() -> None:
    model = ScriptedModel([say("first"), say("second")])
    app = make_chat_app(model)

    async with talk(app) as api:
        first = (await api.post("/v1/chat", json={"message": "hello"})).json()
        second = await api.post(
            "/v1/chat", json={"message": "again", "conversation_id": first["conversation_id"]}
        )

    assert second.json()["conversation_id"] == first["conversation_id"]
    shown_second_time = [m.content for m in model.seen[1] if m.role.value in {"user", "assistant"}]
    assert shown_second_time == ["hello", "first", "again"]


async def test_a_change_is_proposed_by_the_agent_and_confirmed_only_by_the_person() -> None:
    app = make_chat_app(
        ScriptedModel(
            [
                use("propose_add_tester", group_id="group-1", email="new.tester@example.com"),
                say("I have asked for your approval."),
            ]
        )
    )

    async with talk(app) as api:
        body = (await api.post("/v1/chat", json={"message": "add new.tester@example.com"})).json()
        (pending,) = body["pending_actions"]
        confirmed = await api.post(
            f"/v1/actions/{pending['id']}/confirm", json={"channel": "button"}
        )

    assert pending["status"] == "pending"
    assert "new.tester@example.com" in pending["summary"]
    assert confirmed.status_code == 200


async def test_the_agents_own_credential_can_neither_chat_nor_confirm() -> None:
    app = make_chat_app(ScriptedModel([]))
    token = app.state.auth.agent_credential("alice")

    async with talk(app, token) as api:
        chat = await api.post("/v1/chat", json={"message": "hi"})
        confirm = await api.post("/v1/actions/any/confirm", json={"channel": "button"})

    assert (chat.status_code, confirm.status_code) == (403, 403)


async def test_a_client_without_the_chat_scope_is_refused() -> None:
    async with talk(make_chat_app(ScriptedModel([])), NO_CHAT_KEY) as api:
        response = await api.post("/v1/chat", json={"message": "hi"})

    assert response.status_code == 403


async def test_without_a_token_chat_is_unauthorized() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=make_chat_app(ScriptedModel([]))), base_url="http://api"
    ) as api:
        assert (await api.post("/v1/chat", json={"message": "hi"})).status_code == 401


async def test_without_a_configured_model_chat_says_so() -> None:
    async with talk(make_chat_app(None)) as api:
        response = await api.post("/v1/chat", json={"message": "hi"})

    assert response.status_code == 503
    assert response.json()["code"] == "chat_unavailable"


async def test_an_unknown_conversation_is_not_found() -> None:
    async with talk(make_chat_app(ScriptedModel([]))) as api:
        response = await api.post("/v1/chat", json={"message": "hi", "conversation_id": "nope"})

    assert response.status_code == 404


async def test_one_account_cannot_continue_anothers_conversation() -> None:
    app = make_chat_app(ScriptedModel([say("hi alice"), say("must not happen")]))
    async with talk(app) as alice, talk(app, OTHER_KEY) as bob:
        started = (await alice.post("/v1/chat", json={"message": "hello"})).json()
        stolen = await bob.post(
            "/v1/chat",
            json={"message": "hello", "conversation_id": started["conversation_id"]},
        )

    assert stolen.status_code == 404


@pytest.mark.parametrize(
    "body",
    [{"message": ""}, {"message": "x" * 4001}, {}, {"message": "hi", "extra": 1}],
)
async def test_malformed_requests_are_rejected(body: dict[str, object]) -> None:
    async with talk(make_chat_app(ScriptedModel([]))) as api:
        assert (await api.post("/v1/chat", json=body)).status_code == 422


class _Spender(ScriptedModel):
    """Every answer costs a large number of tokens."""

    async def complete(self, messages: Sequence[object], tools: Sequence[object]) -> ModelTurn:
        return ModelTurn(text="ok", usage=Usage(input_tokens=900, output_tokens=200))


async def test_the_daily_token_budget_stops_further_messages() -> None:
    app = make_chat_app(_Spender([]), chat_daily_token_budget=1000)

    async with talk(app) as api:
        first = await api.post("/v1/chat", json={"message": "one"})
        second = await api.post("/v1/chat", json={"message": "two"})

    assert first.status_code == 200
    assert (second.status_code, second.json()["code"]) == (429, "budget_exceeded")
