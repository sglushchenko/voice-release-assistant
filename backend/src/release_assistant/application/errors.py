"""Failures of the confirmation flow, all expected business outcomes."""

from release_assistant.domain.errors import DomainError


class ActionNotPendingError(DomainError):
    """The action was already confirmed, rejected, expired or failed."""


class ActionExpiredError(DomainError):
    """The confirmation window closed before the action was approved."""


class ChannelNotAllowedError(DomainError):
    """The action's approval mode does not accept confirmation from this channel."""
