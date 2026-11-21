"""A language model that replays a script.

Used by tests and by the evaluation harness. Because the model's choices are fixed, a scenario
such as "the model has been hijacked by a review and tries every dangerous thing" can be run
deterministically, for free and in CI, against the real API. It proves what the system does
under a given model behaviour; it says nothing about how well a real model behaves.
"""

import itertools
from collections.abc import Callable, Iterable, Iterator, Sequence
from typing import Any

from release_assistant.agent.errors import ScriptExhaustedError
from release_assistant.agent.messages import Message, ModelTurn, ToolCall, Usage
from release_assistant.agent.tools import ToolSpec

Step = ModelTurn | Callable[[Sequence[Message]], ModelTurn]

_ids = itertools.count(1)

# Fixed token counts so budget behaviour is testable.
_DEFAULT_USAGE = Usage(input_tokens=100, output_tokens=20)


def say(text: str, *, usage: Usage = _DEFAULT_USAGE) -> ModelTurn:
    """A turn that answers in text."""
    return ModelTurn(text=text, usage=usage)


def use(name: str, /, **arguments: Any) -> ModelTurn:
    """A turn that calls one tool."""
    return calls(ToolCall(id=f"call_{next(_ids)}", name=name, arguments=arguments))


def calls(*tool_calls: ToolCall, usage: Usage = _DEFAULT_USAGE) -> ModelTurn:
    """A turn that calls several tools at once."""
    return ModelTurn(tool_calls=tool_calls, usage=usage)


def call(name: str, /, **arguments: Any) -> ToolCall:
    """A tool call to combine with `calls`."""
    return ToolCall(id=f"call_{next(_ids)}", name=name, arguments=arguments)


class ScriptedModel:
    """Replays `script`, one step per model call, and records what it was shown.

    A step may be a ready turn or a function of the conversation so far, for scenarios whose
    next move depends on what the tools returned.
    """

    def __init__(self, script: Iterable[Step]) -> None:
        self._script: Iterator[Step] = iter(script)
        self.seen: list[tuple[Message, ...]] = []

    async def complete(self, messages: Sequence[Message], tools: Sequence[ToolSpec]) -> ModelTurn:
        self.seen.append(tuple(messages))
        step = next(self._script, None)
        if step is None:
            raise ScriptExhaustedError("the scripted model has no more turns")
        return step(messages) if callable(step) else step
