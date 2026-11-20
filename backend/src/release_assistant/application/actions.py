"""Declaration of a risky action: what it accepts, how it is described, what it does."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from release_assistant.application.risk import ActionSpec, RiskTier


@dataclass(frozen=True)
class ActionDefinition:
    """Everything ActionService needs to propose and later execute one action.

    Args:
        spec: Name and risk tier. READ actions are rejected: reads never need confirmation.
        arguments_model: Validates raw arguments before anything is stored (ADR 0003, layer 4).
        describe: Builds the human-readable summary from validated arguments. Async so it can
            look up names (e.g. a tester's email) through read ports. Keep it deterministic and
            model-free; this text is what the user sees when approving.
        handler: Performs the change. Called at most once per pending action.
    """

    spec: ActionSpec
    arguments_model: type[BaseModel]
    describe: Callable[[Any], Awaitable[str]]
    handler: Callable[[Any], Awaitable[Any]]

    def __post_init__(self) -> None:
        if self.spec.tier is RiskTier.READ:
            raise ValueError(f"{self.spec.name!r}: read actions do not go through confirmation")
