"""Decides how strictly an action must be approved. Pure functions, no I/O (ADR 0002)."""

from collections.abc import Mapping
from dataclasses import dataclass

from release_assistant.application.risk import (
    ActionSpec,
    ApprovalMode,
    ConfirmationChannel,
    RiskTier,
)

# Configuration may tighten an action but never go below its tier's floor.
_TIER_FLOOR: Mapping[RiskTier, ApprovalMode] = {
    RiskTier.READ: ApprovalMode.IMPLICIT_IF_CLEAR,
    RiskTier.REVERSIBLE_WRITE: ApprovalMode.IMPLICIT_IF_CLEAR,
    RiskTier.PUBLIC_WRITE: ApprovalMode.VOICE_OR_BUTTON,
    RiskTier.IRREVERSIBLE: ApprovalMode.BUTTON_ONLY,
}


@dataclass(frozen=True)
class ConfirmationContext:
    """Facts about the current session that can only make approval stricter.

    Args:
        untrusted_content_seen: Third-party text (reviews, pasted messages) has entered the
            conversation, so the arguments of a proposed action may have been influenced by it.
    """

    untrusted_content_seen: bool = False


class ConfirmationPolicy:
    """Maps actions to approval modes from configuration plus session context.

    Args:
        modes: Configured mode per action name.
        default: Mode for actions missing from `modes`. Defaults to the strictest (fail closed).
    """

    def __init__(
        self,
        modes: Mapping[str, ApprovalMode] | None = None,
        *,
        default: ApprovalMode = ApprovalMode.BUTTON_ONLY,
    ) -> None:
        self._modes = dict(modes or {})
        self._default = default

    def effective_mode(
        self, spec: ActionSpec, context: ConfirmationContext | None = None
    ) -> ApprovalMode:
        """Resolve the mode at proposal time; the result is stored with the pending action."""
        configured = self._modes.get(spec.name, self._default)
        mode = max(configured, _TIER_FLOOR[spec.tier], key=lambda m: m.strictness)
        if context is not None and context.untrusted_content_seen:
            return ApprovalMode.BUTTON_ONLY
        return mode

    @staticmethod
    def accepts(mode: ApprovalMode, channel: ConfirmationChannel) -> bool:
        """Whether a confirmation from `channel` satisfies `mode`."""
        if channel is ConfirmationChannel.BUTTON:
            return True
        return mode.strictness <= ApprovalMode.VOICE_OR_BUTTON.strictness
