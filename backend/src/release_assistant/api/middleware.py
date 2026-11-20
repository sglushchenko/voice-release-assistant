"""Request correlation and access logging."""

import re
import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

import structlog
from fastapi import FastAPI, Request, Response

log = structlog.get_logger(__name__)

# Accept a client-supplied id only if it is short and plain, so it cannot forge log lines.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def install_request_context(app: FastAPI) -> None:
    """Give every request an id, return it as `X-Request-ID`, bind it to all log lines of the
    request and log one access line with the outcome."""

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        supplied = request.headers.get("x-request-id", "")
        request_id = supplied if _VALID_REQUEST_ID.match(supplied) else uuid4().hex
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
        finally:
            log.info(
                "http.request",
                method=request.method,
                path=request.url.path,
                subject=getattr(request.state, "subject", None),
                status=status,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
            )
        response.headers["X-Request-ID"] = request_id
        return response
