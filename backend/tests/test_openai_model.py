"""The OpenAI adapter, tested against a fake HTTP transport: no network, no real key."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from openai import AsyncOpenAI
from pydantic import SecretStr

from release_assistant.agent.errors import LLMError
from release_assistant.agent.http_backend import HttpToolBackend
from release_assistant.agent.messages import Message, Role, StopReason, ToolCall, ToolResult, Usage
from release_assistant.agent.openai_model import (
    MALFORMED_ARGUMENTS,
    OpenAIModel,
    create_openai_model,
)
from release_assistant.agent.runner import Agent
from release_assistant.agent.tools import DEFAULT_TOOLS, ToolSpec
from release_assistant.config import Settings
from support import AGENT_KEY, http_client, make_app

FAKE_KEY = "sk-test-key-that-is-not-real"
SPEC = ToolSpec(name="list_apps", description="List apps", parameters={"type": "object"})

Handler = Callable[[httpx.Request], httpx.Response]


def completion(
    *,
    content: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    refusal: str | None = None,
    finish_reason: str = "stop",
    prompt_tokens: int = 11,
    completion_tokens: int = 7,
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content, "refusal": refusal}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1_700_000_000,
        "model": "test-model",
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def function_call(call_id: str, name: str, arguments: str) -> dict[str, Any]:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def make_model(handler: Handler, **kwargs: Any) -> OpenAIModel:
    client = AsyncOpenAI(
        api_key=FAKE_KEY,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        max_retries=0,
    )
    return OpenAIModel(client, "test-model", **kwargs)


def replying(payload: dict[str, Any], *, status: int = 200) -> tuple[Handler, list[dict[str, Any]]]:
    """A handler that always answers `payload` and records the JSON body of each request."""
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(status, json=payload)

    return handler, seen


# --- what is sent ----------------------------------------------------------------------


async def test_the_request_carries_the_model_tools_limits_and_privacy_settings() -> None:
    handler, seen = replying(completion(content="hi"))
    model = make_model(handler, max_output_tokens=777)

    await model.complete([Message.user("hello")], [SPEC])

    body = seen[0]
    assert body["model"] == "test-model"
    assert body["max_completion_tokens"] == 777
    assert body["store"] is False
    assert "temperature" not in body  # reasoning models reject non-default values
    assert "reasoning_effort" not in body  # sent only when configured
    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "list_apps",
                "description": "List apps",
                "parameters": {"type": "object"},
            },
        }
    ]


async def test_reasoning_effort_is_sent_only_when_configured() -> None:
    handler, seen = replying(completion(content="hi"))

    await make_model(handler, reasoning_effort="low").complete([Message.user("x")], [SPEC])

    assert seen[0]["reasoning_effort"] == "low"


async def test_a_whole_conversation_maps_onto_openai_messages() -> None:
    handler, seen = replying(completion(content="done"))
    call = ToolCall(id="call_1", name="list_apps", arguments={"name": "pocket"})
    result = ToolResult(call_id="call_1", name="list_apps", content='{"apps": []}')
    conversation = [
        Message.system("be brief"),
        Message.user("my apps?"),
        Message.assistant("", (call,)),
        Message.tool(result, '{"apps": []}'),
        Message.assistant("none"),
        Message.user("thanks"),
    ]

    await make_model(handler).complete(conversation, [SPEC])

    assert seen[0]["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "my apps?"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "list_apps", "arguments": '{"name": "pocket"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": '{"apps": []}'},
        {"role": "assistant", "content": "none"},
        {"role": "user", "content": "thanks"},
    ]


async def test_every_catalogue_tool_is_accepted_as_an_openai_function() -> None:
    handler, seen = replying(completion(content="ok"))
    specs = [tool.spec for tool in DEFAULT_TOOLS]

    await make_model(handler).complete([Message.user("x")], specs)

    sent = seen[0]["tools"]
    assert [t["function"]["name"] for t in sent] == [s.name for s in specs]
    assert all(t["type"] == "function" for t in sent)


# --- what comes back -------------------------------------------------------------------


async def test_a_text_reply_becomes_a_turn_with_its_token_usage() -> None:
    handler, _ = replying(completion(content="Two apps.", prompt_tokens=120, completion_tokens=30))

    turn = await make_model(handler).complete([Message.user("x")], [SPEC])

    assert turn.text == "Two apps."
    assert turn.tool_calls == ()
    assert turn.usage == Usage(input_tokens=120, output_tokens=30)


async def test_tool_calls_become_parsed_calls_with_their_ids() -> None:
    payload = completion(
        tool_calls=[
            function_call("call_a", "list_apps", "{}"),
            function_call("call_b", "latest_build", '{"app_id": "app-1"}'),
        ]
    )
    handler, _ = replying(payload)

    turn = await make_model(handler).complete([Message.user("x")], [SPEC])

    assert [(c.id, c.name, c.arguments) for c in turn.tool_calls] == [
        ("call_a", "list_apps", {}),
        ("call_b", "latest_build", {"app_id": "app-1"}),
    ]


@pytest.mark.parametrize("broken", ["{not json", "[1, 2]", '"text"'])
async def test_arguments_that_are_not_a_json_object_are_flagged_for_the_backend(
    broken: str,
) -> None:
    handler, _ = replying(completion(tool_calls=[function_call("c", "list_apps", broken)]))

    turn = await make_model(handler).complete([Message.user("x")], [SPEC])

    assert turn.tool_calls[0].arguments == MALFORMED_ARGUMENTS


async def test_a_refusal_is_the_models_text_answer() -> None:
    handler, _ = replying(completion(content=None, refusal="I can't help with that."))

    turn = await make_model(handler).complete([Message.user("x")], [SPEC])

    assert turn.text == "I can't help with that."


async def test_an_empty_reply_is_an_error_naming_the_finish_reason() -> None:
    handler, _ = replying(completion(content=None, finish_reason="length"))

    with pytest.raises(LLMError, match="length"):
        await make_model(handler).complete([Message.user("x")], [SPEC])


# --- failures --------------------------------------------------------------------------


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500, 503])
async def test_provider_errors_become_llm_errors_that_never_quote_the_provider(
    status: int,
) -> None:
    leaky = {"error": {"message": "Incorrect API key provided: sk-live-SECRET123", "type": "x"}}
    handler, _ = replying(leaky, status=status)

    with pytest.raises(LLMError) as raised:
        await make_model(handler).complete([Message.user("x")], [SPEC])

    assert "SECRET123" not in str(raised.value)
    assert FAKE_KEY not in str(raised.value)


async def test_a_dropped_connection_becomes_an_llm_error() -> None:
    def refuse(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(LLMError, match="APIConnectionError"):
        await make_model(refuse).complete([Message.user("x")], [SPEC])


async def test_a_timeout_becomes_an_llm_error() -> None:
    def too_slow(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    with pytest.raises(LLMError, match="Timeout"):
        await make_model(too_slow).complete([Message.user("x")], [SPEC])


async def test_a_response_without_choices_is_an_error() -> None:
    handler, _ = replying({**completion(content="x"), "choices": []})

    with pytest.raises(LLMError, match="no choices"):
        await make_model(handler).complete([Message.user("x")], [SPEC])


# --- configuration ---------------------------------------------------------------------


def test_without_a_key_the_factory_names_the_setting_to_fix() -> None:
    with pytest.raises(LLMError, match="RELEASE_ASSISTANT_OPENAI_API_KEY"):
        create_openai_model(Settings(_env_file=None))


def test_with_a_key_the_factory_builds_a_model_without_calling_anything() -> None:
    settings = Settings(_env_file=None, openai_api_key=SecretStr(FAKE_KEY), openai_model="m")

    assert isinstance(create_openai_model(settings), OpenAIModel)


def test_the_key_never_appears_in_the_settings_text() -> None:
    settings = Settings(_env_file=None, openai_api_key=SecretStr(FAKE_KEY))

    assert FAKE_KEY not in repr(settings)
    assert FAKE_KEY not in str(settings)
    assert FAKE_KEY not in settings.model_dump_json()


def test_defaults_are_conservative() -> None:
    settings = Settings(_env_file=None)

    assert settings.openai_api_key is None
    assert settings.openai_reasoning_effort is None
    assert settings.openai_max_output_tokens == 2000
    assert settings.openai_timeout_seconds == 30


# --- inside the agent loop -------------------------------------------------------------


async def test_the_adapter_drives_a_full_agent_run_against_the_real_api() -> None:
    """A tool call, its result sent back with the right id, then the final answer."""
    requests: list[dict[str, Any]] = []
    replies = iter(
        [
            completion(tool_calls=[function_call("call_xyz", "list_apps", "{}")]),
            completion(content="You have Pocket Notes and Habit Garden."),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=next(replies))

    async with http_client(make_app(), AGENT_KEY) as api:
        backend = await HttpToolBackend.open(api)
        run = await Agent(make_model(handler), backend).respond([], "what apps do I have?")

    assert run.stop_reason is StopReason.ANSWERED
    assert run.answer == "You have Pocket Notes and Habit Garden."
    assert run.usage.total == 36
    returned = requests[1]["messages"][-1]
    assert returned["role"] == "tool"
    assert returned["tool_call_id"] == "call_xyz"
    assert "Pocket Notes" in returned["content"]
    assert [m.role for m in run.new_messages] == [
        Role.USER,
        Role.ASSISTANT,
        Role.TOOL,
        Role.ASSISTANT,
    ]


async def test_a_provider_failure_mid_run_ends_it_cleanly() -> None:
    handler, _ = replying({"error": {"message": "overloaded"}}, status=503)

    async with http_client(make_app(), AGENT_KEY) as api:
        backend = await HttpToolBackend.open(api)
        run = await Agent(make_model(handler), backend).respond([], "hi")

    assert run.stop_reason is StopReason.MODEL_ERROR
    assert "overloaded" not in run.answer
