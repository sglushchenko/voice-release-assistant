"""One error shape for the whole API: a problem-details JSON body (ADR 0004).

Messages come from domain errors, which carry identifiers and rules but never internals.
Unexpected failures return a generic 500 and are logged with the request id instead.
"""

from http import HTTPStatus

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from release_assistant.application.errors import (
    ActionExpiredError,
    ActionNotPendingError,
    ChannelNotAllowedError,
    TooManySessionsError,
)
from release_assistant.domain.errors import (
    AlreadyExistsError,
    DataUnavailableError,
    DomainError,
    NotFoundError,
)

log = structlog.get_logger(__name__)

_DOMAIN_STATUS: tuple[tuple[type[DomainError], int, str], ...] = (
    (NotFoundError, 404, "not_found"),
    (AlreadyExistsError, 409, "already_exists"),
    (ActionNotPendingError, 409, "action_not_pending"),
    (ActionExpiredError, 410, "action_expired"),
    (ChannelNotAllowedError, 403, "channel_not_allowed"),
    (TooManySessionsError, 429, "too_many_sessions"),
    (DataUnavailableError, 503, "data_unavailable"),
)


class Problem(BaseModel):
    """The body of every error response (problem-details shape). Exists so the API contract
    describes errors, letting generated clients decode them."""

    title: str
    status: int
    code: str
    detail: str


class ApiError(Exception):
    """Raise for failures that belong to the transport (auth, request shape)."""

    def __init__(
        self, status_code: int, code: str, detail: str, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail
        self.headers = headers


def problem(
    status_code: int, code: str, detail: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    body = {
        "title": HTTPStatus(status_code).phrase,
        "status": status_code,
        "code": code,
        "detail": detail,
    }
    return JSONResponse(
        body, status_code=status_code, headers=headers, media_type="application/problem+json"
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return problem(exc.status_code, exc.code, exc.detail, exc.headers)

    @app.exception_handler(DomainError)
    async def _domain_error(_: Request, exc: DomainError) -> JSONResponse:
        for error_type, status_code, code in _DOMAIN_STATUS:
            if isinstance(exc, error_type):
                return problem(status_code, code, str(exc))
        return problem(400, "domain_error", str(exc))

    @app.exception_handler(RequestValidationError)
    async def _invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Only location and message: the offending input is never echoed back.
        detail = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        return problem(422, "invalid_request", detail)

    @app.exception_handler(ValidationError)
    async def _invalid_arguments(_: Request, exc: ValidationError) -> JSONResponse:
        """Action arguments are validated inside the application, not by FastAPI."""
        detail = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        return problem(422, "invalid_arguments", detail)

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        log.error("http.unhandled_error", exc_info=exc)
        return problem(500, "internal_error", "Internal server error")
