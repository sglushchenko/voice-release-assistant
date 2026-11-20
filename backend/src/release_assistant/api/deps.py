"""FastAPI dependencies: the application, the caller and scope checks."""

from collections.abc import Awaitable, Callable
from typing import Annotated

import structlog
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from release_assistant.api.auth import Principal
from release_assistant.api.errors import ApiError
from release_assistant.api.login import AuthService
from release_assistant.bootstrap import Application

log = structlog.get_logger(__name__)
bearer_scheme = HTTPBearer(auto_error=False)
_UNAUTHORIZED = {"WWW-Authenticate": "Bearer"}


def get_application(request: Request) -> Application:
    return request.app.state.application


def get_auth_service(request: Request) -> AuthService:
    return request.app.state.auth


ApplicationDep = Annotated[Application, Depends(get_application)]
AuthDep = Annotated[AuthService, Depends(get_auth_service)]


async def get_principal(
    request: Request,
    auth: AuthDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> Principal:
    """Async on purpose: binding the subject to the log context must happen in the request's
    own task, not in a worker thread. The subject is also kept on `request.state`, which the
    access-log middleware can read; context variables set here do not reach it because it
    runs in a different task."""
    if credentials is None:
        log.warning("auth.rejected", reason="missing_credentials")
        raise ApiError(401, "unauthorized", "Missing bearer token", _UNAUTHORIZED)
    principal = auth.authenticate(credentials.credentials)
    if principal is None:
        log.warning("auth.rejected", reason="invalid_credentials")
        raise ApiError(401, "unauthorized", "Invalid bearer token", _UNAUTHORIZED)
    structlog.contextvars.bind_contextvars(subject=principal.subject, owner=principal.owner)
    request.state.subject = principal.subject
    return principal


PrincipalDep = Annotated[Principal, Depends(get_principal)]


def ensure_scope(principal: Principal, *scopes: str) -> None:
    """Raise 403 unless the principal holds at least one of `scopes`."""
    if not principal.scopes.intersection(scopes):
        log.warning("auth.forbidden", needs_one_of=sorted(scopes))
        raise ApiError(403, "forbidden", f"Missing scope: {' or '.join(sorted(scopes))}")


def require_scope(scope: str) -> Callable[..., Awaitable[Principal]]:
    """Dependency factory: the caller must hold `scope`. Returns the principal."""

    async def dependency(principal: PrincipalDep) -> Principal:
        ensure_scope(principal, scope)
        return principal

    return dependency
