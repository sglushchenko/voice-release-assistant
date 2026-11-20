"""The vocabulary of a conversation: messages, tool calls, model turns and run outcomes.

All types are immutable. They are plain data shared by the loop, the model adapters and the
conversation store, so a stored conversation can be replayed to any provider.
"""

from enum import StrEnum
from typing import Any, Self

from pydantic import Field, model_validator

from release_assistant.domain.entities import Entity


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class Trust(StrEnum):
    """Whether text may contain instructions aimed at the model. Third-party text (customer
    reviews) is untrusted: it is data to report on, never orders to follow (ADR 0003)."""

    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"


class ToolCall(Entity):
    """The model's request to run a tool. `id` pairs it with its result."""

    id: str
    name: str
    arguments: dict[str, Any]


class ToolResult(Entity):
    """What running a tool produced.

    Args:
        content: Text for the model, normally the API's JSON response.
        trust: UNTRUSTED when the API labelled the payload as third-party text.
        is_error: The call failed; `content` says why, so the model can adjust.
        pending_action_id: Set when the call proposed a change that awaits a person's
            confirmation; the run reports these to the caller.
    """

    call_id: str
    name: str
    content: str
    trust: Trust = Trust.TRUSTED
    is_error: bool = False
    pending_action_id: str | None = None


class Message(Entity):
    """One entry of a conversation.

    For TOOL messages, `content` is the text the model sees (an untrusted payload is wrapped
    and labelled there) while `tool_result` keeps the structured outcome, including its trust.
    """

    role: Role
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_result: ToolResult | None = None

    @model_validator(mode="after")
    def _check_shape(self) -> Self:
        if self.role is Role.TOOL and self.tool_result is None:
            raise ValueError("a tool message needs a tool_result")
        if self.role is not Role.TOOL and self.tool_result is not None:
            raise ValueError("only tool messages carry a tool_result")
        if self.role is not Role.ASSISTANT and self.tool_calls:
            raise ValueError("only assistant messages carry tool calls")
        return self

    @classmethod
    def system(cls, text: str) -> Self:
        return cls(role=Role.SYSTEM, content=text)

    @classmethod
    def user(cls, text: str) -> Self:
        return cls(role=Role.USER, content=text)

    @classmethod
    def assistant(cls, text: str = "", tool_calls: tuple[ToolCall, ...] = ()) -> Self:
        return cls(role=Role.ASSISTANT, content=text, tool_calls=tool_calls)

    @classmethod
    def tool(cls, result: ToolResult, rendered: str) -> Self:
        return cls(role=Role.TOOL, content=rendered, tool_result=result)


class Usage(Entity):
    """Token counts, summed across a run to enforce a budget."""

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


class ModelTurn(Entity):
    """One reply of the model: text, tool calls, or both."""

    text: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    usage: Usage = Usage()

    @model_validator(mode="after")
    def _not_empty(self) -> Self:
        if not self.text and not self.tool_calls:
            raise ValueError("a model turn needs text or at least one tool call")
        return self


class StopReason(StrEnum):
    """Why a run ended. Anything but ANSWERED means the loop stopped it, not the model."""

    ANSWERED = "answered"
    STEP_LIMIT = "step_limit"
    TOKEN_BUDGET = "token_budget"  # noqa: S105  # a budget of tokens, not a secret
    MODEL_ERROR = "model_error"


class AgentRun(Entity):
    """The outcome of one user message.

    Args:
        answer: What to show the user. When the loop stopped the run, this is a fixed safe
            message, never model output.
        new_messages: Everything this run added to the conversation, for the caller to store.
        pending_action_ids: Changes the agent proposed that still need a person's confirmation.
    """

    answer: str
    stop_reason: StopReason
    steps: int
    tool_calls_made: int
    pending_action_ids: tuple[str, ...]
    usage: Usage
    new_messages: tuple[Message, ...]
