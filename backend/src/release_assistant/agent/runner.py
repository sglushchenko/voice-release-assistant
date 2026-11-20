"""The agent loop: model, tools, model, until an answer or a limit.

Every limit here is enforced by code. The model is never trusted to stop itself, to pick only
real tools, or to keep within a budget.
"""

from collections.abc import Sequence

import structlog

from release_assistant.agent.errors import LLMError
from release_assistant.agent.messages import (
    AgentRun,
    Message,
    StopReason,
    ToolCall,
    ToolResult,
    Usage,
)
from release_assistant.agent.ports import LLMClient, ToolBackend
from release_assistant.agent.prompt import SYSTEM_PROMPT
from release_assistant.agent.rendering import render_tool_result
from release_assistant.agent.tools import DEFAULT_TOOLS, Tool, ToolSpec
from release_assistant.domain.entities import Entity

log = structlog.get_logger(__name__)

# Shown when the loop, not the model, ended the run. Fixed text: nothing a model wrote.
_STOPPED = {
    StopReason.STEP_LIMIT: "I could not finish this within the allowed number of steps.",
    StopReason.TOKEN_BUDGET: "I stopped because this request used more than its allowed budget.",
    StopReason.MODEL_ERROR: "I could not get an answer from the language model. Please try again.",
}


class AgentLimits(Entity):
    """Ceilings for one user message.

    Args:
        max_steps: Model calls allowed.
        max_tool_calls_per_step: Tool calls honoured in one model turn; extra ones are refused.
        max_total_tokens: Tokens, input plus output, across the run.
    """

    max_steps: int = 8
    max_tool_calls_per_step: int = 5
    max_total_tokens: int = 60_000


class Agent:
    """
    Args:
        model: The language model; any LLMClient.
        tools: Where tool calls are executed.
        system_prompt: Guidance for the model.
        limits: Ceilings applied to every run.
        offered: The tools shown to the model. Must match what `tools` can run.
    """

    def __init__(
        self,
        model: LLMClient,
        tools: ToolBackend,
        *,
        system_prompt: str = SYSTEM_PROMPT,
        limits: AgentLimits | None = None,
        offered: Sequence[Tool] = DEFAULT_TOOLS,
    ) -> None:
        self._model = model
        self._tools = tools
        self._system_prompt = system_prompt
        self._limits = limits or AgentLimits()
        self._specs: tuple[ToolSpec, ...] = tuple(tool.spec for tool in offered)

    async def respond(self, history: Sequence[Message], user_text: str) -> AgentRun:
        """Answer one user message in the context of `history`.

        Nothing is mutated: the messages this run adds are returned in `new_messages` for the
        caller to store.
        """
        added: list[Message] = [Message.user(user_text)]
        context = [Message.system(self._system_prompt), *history]
        usage = Usage()
        tool_calls_made = 0
        pending: list[str] = []

        for step in range(1, self._limits.max_steps + 1):
            try:
                turn = await self._model.complete([*context, *added], self._specs)
            except LLMError:
                log.warning("agent.model_error", step=step)
                return self._stopped(
                    StopReason.MODEL_ERROR, step, tool_calls_made, pending, usage, added
                )

            usage = usage + turn.usage
            added.append(Message.assistant(turn.text or "", turn.tool_calls))
            log.info(
                "agent.step",
                step=step,
                tools=[call.name for call in turn.tool_calls],
                input_tokens=turn.usage.input_tokens,
                output_tokens=turn.usage.output_tokens,
            )

            if not turn.tool_calls:
                return AgentRun(
                    answer=turn.text or "",
                    stop_reason=StopReason.ANSWERED,
                    steps=step,
                    tool_calls_made=tool_calls_made,
                    pending_action_ids=tuple(pending),
                    usage=usage,
                    new_messages=tuple(added),
                )

            for index, call in enumerate(turn.tool_calls):
                result = await self._run_tool(
                    call, over_limit=index >= self._limits.max_tool_calls_per_step
                )
                tool_calls_made += 1
                if result.pending_action_id:
                    pending.append(result.pending_action_id)
                added.append(Message.tool(result, render_tool_result(result)))

            if usage.total > self._limits.max_total_tokens:
                return self._stopped(
                    StopReason.TOKEN_BUDGET, step, tool_calls_made, pending, usage, added
                )

        return self._stopped(
            StopReason.STEP_LIMIT, self._limits.max_steps, tool_calls_made, pending, usage, added
        )

    async def _run_tool(self, call: ToolCall, *, over_limit: bool) -> ToolResult:
        """Every call needs a result, even a refused one: providers reject a conversation in
        which a tool call has no matching answer."""
        if over_limit:
            log.warning("agent.tool_refused", tool=call.name, reason="too_many_calls")
            return ToolResult(
                call_id=call.id,
                name=call.name,
                content='{"error": "Too many tool calls in one step; this one was not run."}',
                is_error=True,
            )
        result = await self._tools.call(call)
        log.info(
            "agent.tool_called",
            tool=call.name,
            is_error=result.is_error,
            untrusted=result.trust.value == "untrusted",
            proposed=result.pending_action_id is not None,
        )
        return result

    @staticmethod
    def _stopped(
        reason: StopReason,
        steps: int,
        tool_calls_made: int,
        pending: list[str],
        usage: Usage,
        added: list[Message],
    ) -> AgentRun:
        log.warning("agent.stopped", reason=reason.value, steps=steps, tokens=usage.total)
        answer = _STOPPED[reason]
        return AgentRun(
            answer=answer,
            stop_reason=reason,
            steps=steps,
            tool_calls_made=tool_calls_made,
            pending_action_ids=tuple(pending),
            usage=usage,
            new_messages=(*added, Message.assistant(answer)),
        )
