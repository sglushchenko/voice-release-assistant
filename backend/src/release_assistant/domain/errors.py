"""Domain errors. Adapters translate transport-level failures into these so callers
never depend on HTTP status codes or provider-specific exceptions."""


class DomainError(Exception):
    """Base class; catch this to handle any expected business failure."""


class NotFoundError(DomainError):
    """The referenced app, group, tester or review does not exist."""


class AlreadyExistsError(DomainError):
    """The change would duplicate state (tester already in group, review already answered)."""
