"""The agent's two replaceable edges.

`LLMClient` is the model: a scripted double in tests, a provider adapter in production.
`ToolBackend` is the world: it runs tool calls against the assistant's API. Nothing else in
the agent knows which implementations are plugged in.
"""

from collections.abc import Sequence
from typing import Protocol

from release_assistant.agent.messages import Message, ModelTurn, ToolCall, ToolResult
from release_assistant.agent.tools import ToolSpec


class LLMClient(Protocol):
    """One step of a conversation."""

    async def complete(self, messages: Sequence[Message], tools: Sequence[ToolSpec]) -> ModelTurn:
        """The model's next turn given the conversation so far and the tools it may call.

        Raises LLMError for any provider failure.
        """
        ...


class ToolBackend(Protocol):
    """Runs tools. There is deliberately no way to confirm an action through it: confirmation
    is a person's act, performed by another client with another credential."""

    async def call(self, call: ToolCall) -> ToolResult:
        """Run a tool call. Failures come back as a result with `is_error` set, never as an
        exception, so the model can read the problem and adjust."""
        ...
