"""FastAPI dependencies: the application, the caller and scope checks."""

from collections.abc import Awaitable, Callable
from typing import Annotated

import structlog
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from release_assistant.api.auth import Authenticator, Principal
from release_assistant.api.errors import ApiError
from release_assistant.bootstrap import Application

log = structlog.get_logger(__name__)
_bearer = HTTPBearer(auto_error=False)
_UNAUTHORIZED = {"WWW-Authenticate": "Bearer"}


def get_application(request: Request) -> Application:
    return request.app.state.application


def get_authenticator(request: Request) -> Authenticator:
    return request.app.state.authenticator


ApplicationDep = Annotated[Application, Depends(get_application)]


async def get_principal(
    request: Request,
    authenticator: Annotated[Authenticator, Depends(get_authenticator)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    """Async on purpose: binding the subject to the log context must happen in the request's
    own task, not in a worker thread. The subject is also kept on `request.state`, which the
    access-log middleware can read; context variables set here do not reach it because it
    runs in a different task."""
    if credentials is None:
        log.warning("auth.rejected", reason="missing_credentials")
        raise ApiError(401, "unauthorized", "Missing bearer token", _UNAUTHORIZED)
    principal = authenticator.authenticate(credentials.credentials)
    if principal is None:
        log.warning("auth.rejected", reason="invalid_credentials")
        raise ApiError(401, "unauthorized", "Invalid bearer token", _UNAUTHORIZED)
    structlog.contextvars.bind_contextvars(subject=principal.subject)
    request.state.subject = principal.subject
    return principal


def require_scope(scope: str) -> Callable[..., Awaitable[Principal]]:
    """Dependency factory: the caller must hold `scope`."""

    async def dependency(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
        if scope not in principal.scopes:
            log.warning("auth.forbidden", missing_scope=scope)
            raise ApiError(403, "forbidden", f"Missing scope: {scope}")
        return principal

    return dependency
