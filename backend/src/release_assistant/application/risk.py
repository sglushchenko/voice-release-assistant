"""Vocabulary for classifying actions and deciding how they may be approved (ADR 0002)."""

from dataclasses import dataclass
from enum import StrEnum


class RiskTier(StrEnum):
    """How much damage an action can do. Fixed by the developer per action, never by config."""

    READ = "read"
    REVERSIBLE_WRITE = "reversible_write"
    PUBLIC_WRITE = "public_write"
    IRREVERSIBLE = "irreversible"


class ApprovalMode(StrEnum):
    """How an action may be approved, from least to most strict (the order of definition).

    Values are what clients see in the API; `strictness` is what the policy compares.
    """

    IMPLICIT_IF_CLEAR = "implicit_if_clear"
    VOICE_OR_BUTTON = "voice_or_button"
    BUTTON_ONLY = "button_only"

    @property
    def strictness(self) -> int:
        """0 for the most permissive mode, higher for stricter ones."""
        return list(type(self)).index(self)


class ConfirmationChannel(StrEnum):
    """Where a confirmation came from. Set by the client or transport, never by a model."""

    VOICE = "voice"
    BUTTON = "button"


@dataclass(frozen=True)
class ActionSpec:
    """Identity and risk of an action; the only inputs the policy needs."""

    name: str
    tier: RiskTier
