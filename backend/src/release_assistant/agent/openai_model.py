"""The OpenAI language model behind the agent's `LLMClient` protocol (ADR 0008).

Uses the Chat Completions API: the agent keeps its own provider-neutral history, and these
messages map onto it one to one, with nothing provider-specific to carry between turns.
"""

import json
from collections.abc import Sequence
from typing import Any

import openai
import structlog
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion, ChatCompletionMessageParam, ChatCompletionToolParam

from release_assistant.agent.errors import LLMError
from release_assistant.agent.messages import Message, ModelTurn, Role, ToolCall, Usage
from release_assistant.agent.tools import ToolSpec
from release_assistant.config import Settings

log = structlog.get_logger(__name__)

# What the adapter gives the tool backend when the model produced arguments that are not JSON.
# The backend refuses it as an unknown argument, and the model reads that and tries again.
MALFORMED_ARGUMENTS = {"_malformed_arguments": True}


class OpenAIModel:
    """
    Args:
        client: An OpenAI client; the caller owns its key, timeout and retry policy.
        model: Model name as the account lists it.
        max_output_tokens: Ceiling on one reply, reasoning included.
        reasoning_effort: Sent only when set, because models without reasoning reject it.
    """

    def __init__(
        self,
        client: AsyncOpenAI,
        model: str,
        *,
        max_output_tokens: int = 2000,
        reasoning_effort: str | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._max_output_tokens = max_output_tokens
        self._reasoning_effort = reasoning_effort

    async def complete(self, messages: Sequence[Message], tools: Sequence[ToolSpec]) -> ModelTurn:
        request: dict[str, Any] = {
            "model": self._model,
            "messages": [_to_openai_message(m) for m in messages],
            "tools": [_to_openai_tool(t) for t in tools],
            "max_completion_tokens": self._max_output_tokens,
            # Conversation content is not retained by the provider.
            "store": False,
        }
        if self._reasoning_effort is not None:
            request["reasoning_effort"] = self._reasoning_effort
        try:
            response = await self._client.chat.completions.create(**request)
        except openai.OpenAIError as error:
            # Only the kind of failure is kept: provider messages can quote part of the API key.
            log.warning(
                "agent.llm_failed",
                kind=type(error).__name__,
                status=getattr(error, "status_code", None),
            )
            raise LLMError(f"the model request failed ({type(error).__name__})") from None
        return _to_turn(response)


def create_openai_model(settings: Settings) -> OpenAIModel:
    """Build the model from configuration. Raises LLMError when no key is configured, naming
    the setting to fix and never printing any value."""
    if settings.openai_api_key is None:
        raise LLMError("OpenAI is not configured: set RELEASE_ASSISTANT_OPENAI_API_KEY")
    client = AsyncOpenAI(
        api_key=settings.openai_api_key.get_secret_value(),
        base_url=settings.openai_base_url,
        timeout=settings.openai_timeout_seconds,
        max_retries=2,
    )
    return OpenAIModel(
        client,
        settings.openai_model,
        max_output_tokens=settings.openai_max_output_tokens,
        reasoning_effort=settings.openai_reasoning_effort,
    )


def _to_openai_message(message: Message) -> ChatCompletionMessageParam:
    if message.role is Role.SYSTEM:
        return {"role": "system", "content": message.content}
    if message.role is Role.USER:
        return {"role": "user", "content": message.content}
    if message.role is Role.TOOL:
        if message.tool_result is None:  # Message validation makes this unreachable
            raise LLMError("a tool message without a result cannot be sent")
        return {
            "role": "tool",
            "tool_call_id": message.tool_result.call_id,
            "content": message.content,
        }
    if not message.tool_calls:
        return {"role": "assistant", "content": message.content}
    return {
        "role": "assistant",
        "content": message.content or None,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
            }
            for call in message.tool_calls
        ],
    }


def _to_openai_tool(spec: ToolSpec) -> ChatCompletionToolParam:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
        },
    }


def _to_turn(response: ChatCompletion) -> ModelTurn:
    if not response.choices:
        raise LLMError("the model returned no choices")
    choice = response.choices[0]
    message = choice.message

    tool_calls = tuple(
        ToolCall(id=call.id, name=call.function.name, arguments=_arguments(call.function.arguments))
        for call in message.tool_calls or ()
        if call.type == "function"
    )
    # A refusal is the model's text answer, shown as such.
    text = message.content or message.refusal or None
    usage = Usage(
        input_tokens=response.usage.prompt_tokens if response.usage else 0,
        output_tokens=response.usage.completion_tokens if response.usage else 0,
    )
    if not text and not tool_calls:
        # Typically the output ceiling was reached while the model was still thinking.
        raise LLMError(f"the model returned nothing (finish_reason={choice.finish_reason})")
    return ModelTurn(text=text, tool_calls=tool_calls, usage=usage)


def _arguments(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
    except ValueError:
        return dict(MALFORMED_ARGUMENTS)
    return parsed if isinstance(parsed, dict) else dict(MALFORMED_ARGUMENTS)
