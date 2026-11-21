"""Domain errors. Adapters translate transport-level failures into these so callers
never depend on HTTP status codes or provider-specific exceptions."""


class DomainError(Exception):
    """Base class; catch this to handle any expected business failure."""


class NotFoundError(DomainError):
    """The referenced app, group, tester or review does not exist."""


class DataUnavailableError(DomainError):
    """The source has nothing published yet, e.g. reports are still generating after the
    first request."""


class NotSupportedError(DomainError):
    """The backend cannot do this yet (a stubbed capability). The message says what, so a
    caller, or a model reporting to a person, can tell them plainly."""


class UpstreamError(DomainError):
    """The system behind a backend failed or refused: bad credentials, a missing permission,
    a rate limit, an outage. The message is safe to show: it never contains a credential."""


class AlreadyExistsError(DomainError):
    """The change would duplicate state (tester already in group, review already answered)."""
