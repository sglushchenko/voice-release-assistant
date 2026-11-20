"""Named rate-limit policies on top of the `limits` library (ADR 0006).

`limits` does the counting (moving-window algorithm, memory or Redis storage). This module
adds what is specific to the service: which policy applies where, keyed by address or by
caller, answered in the API's error format.
"""

import math
import time
from collections.abc import Awaitable, Callable

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import Response
from limits import RateLimitItem, parse_many
from limits.aio.strategies import MovingWindowRateLimiter
from limits.storage import storage_from_string

from release_assistant.api.errors import ApiError, problem
from release_assistant.config import RateLimitPolicy, Settings

log = structlog.get_logger(__name__)

# The liveness probe is called constantly by the platform and must never be throttled.
_EXEMPT_PATHS = frozenset({"/healthz"})


class RateLimits:
    """All policies behind one call.

    Args:
        settings: Defaults, overrides, storage and the on/off switch.
    """

    def __init__(self, settings: Settings) -> None:
        self._enabled = settings.rate_limit_enabled
        self._strategy = MovingWindowRateLimiter(storage_from_string(settings.rate_limit_storage))
        self._limits = {
            policy: parse_many(spec) for policy, spec in settings.effective_rate_limits.items()
        }

    async def enforce(self, policy: RateLimitPolicy, key: str) -> None:
        """Count one request for `key` under `policy`, or raise a 429 saying when to retry.

        A policy may hold several limits ("20/second;600/minute"). All are checked before
        any is counted, so a refused request does not use up the allowance of the others.
        """
        if not self._enabled:
            return
        items = self._limits[policy]
        for item in items:
            if not await self._strategy.test(item, policy, key):
                await self._refuse(policy, key, item)
        for item in items:
            if not await self._strategy.hit(item, policy, key):
                await self._refuse(policy, key, item)

    async def _refuse(self, policy: RateLimitPolicy, key: str, item: RateLimitItem) -> None:
        stats = await self._strategy.get_window_stats(item, policy, key)
        wait = max(1, math.ceil(stats.reset_time - time.time()))
        log.warning("rate.limited", policy=policy, key=key)
        raise ApiError(
            429, "rate_limited", "Too many requests; slow down", {"Retry-After": str(wait)}
        )


def client_address(request: Request) -> str:
    """The connection's address. Uvicorn already replaced it with `X-Forwarded-For` when the
    connection came from a trusted proxy, so this is safe to use as a limiter key."""
    return request.client.host if request.client else "unknown"


def install_address_rate_limit(app: FastAPI) -> None:
    """Limit every request per client address before authentication or routing work."""

    @app.middleware("http")
    async def address_rate_limit(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path not in _EXEMPT_PATHS:
            try:
                await request.app.state.limits.enforce("ip", client_address(request))
            except ApiError as error:
                return problem(error.status_code, error.code, error.detail, error.headers)
        return await call_next(request)
