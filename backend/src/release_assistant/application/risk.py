"""Vocabulary for classifying actions and deciding how they may be approved (ADR 0002)."""

from dataclasses import dataclass
from enum import IntEnum, StrEnum


class RiskTier(StrEnum):
    """How much damage an action can do. Fixed by the developer per action, never by config."""

    READ = "read"
    REVERSIBLE_WRITE = "reversible_write"
    PUBLIC_WRITE = "public_write"
    IRREVERSIBLE = "irreversible"


class ApprovalMode(IntEnum):
    """How an action may be approved. A higher value is stricter, so `max()` tightens."""

    IMPLICIT_IF_CLEAR = 0
    VOICE_OR_BUTTON = 1
    BUTTON_ONLY = 2


class ConfirmationChannel(StrEnum):
    """Where a confirmation came from. Set by the client or transport, never by a model."""

    VOICE = "voice"
    BUTTON = "button"


@dataclass(frozen=True)
class ActionSpec:
    """Identity and risk of an action; the only inputs the policy needs."""

    name: str
    tier: RiskTier
