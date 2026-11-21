"""Failures of the agent machinery, kept apart from the domain's business errors."""

from release_assistant.domain.errors import DomainError


class AgentError(Exception):
    """Base class for agent failures."""


class LLMError(AgentError):
    """The model provider failed or returned something unusable. Adapters raise this so the
    loop can end the run cleanly instead of crashing on a provider-specific exception."""


class ToolBackendError(AgentError):
    """The tool backend could not be set up (for example, a session could not be opened)."""


class ScriptExhaustedError(AgentError):
    """A scripted test model was asked for more turns than it was given."""


class ChatUnavailableError(DomainError):
    """Chat cannot answer right now, for example because no model is configured."""


class TokenBudgetExceededError(DomainError):
    """The account has used its daily allowance of model tokens."""
