"""Token-bucket rate limiting (ADR 0006). In memory, single process, bounded."""

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import Response

from release_assistant.api.errors import ApiError, problem
from release_assistant.config import Settings

log = structlog.get_logger(__name__)

Policy = Literal["ip", "principal", "login", "propose"]

# The liveness probe is called constantly by the platform and must never be throttled.
_EXEMPT_PATHS = frozenset({"/healthz"})


@dataclass(frozen=True)
class Rate:
    """`per_second` tokens are added steadily, up to `burst` stored for spikes."""

    per_second: float
    burst: int

    def __post_init__(self) -> None:
        if self.per_second <= 0 or self.burst < 1:
            raise ValueError("rate needs a positive speed and a burst of at least 1")


@dataclass
class _Bucket:
    tokens: float
    updated: float


class TokenBucketLimiter:
    """One bucket per key. A request spends a token; with none left it must wait.

    Args:
        rate: Refill speed and burst size.
        clock: Monotonic seconds; injected so tests control time.
        max_keys: Buckets kept. Beyond this, idle full buckets go first, then the stalest.
    """

    def __init__(
        self,
        rate: Rate,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = 10_000,
    ) -> None:
        self._rate = rate
        self._clock = clock
        self._max_keys = max_keys
        self._buckets: dict[str, _Bucket] = {}

    def check(self, key: str) -> float | None:
        """None if the request may proceed (and spends a token), else seconds to wait."""
        now = self._clock()
        bucket = self._buckets.get(key)
        if bucket is None:
            self._make_room(now)
            bucket = _Bucket(tokens=float(self._rate.burst), updated=now)
            self._buckets[key] = bucket
        else:
            elapsed = now - bucket.updated
            bucket.tokens = min(self._rate.burst, bucket.tokens + elapsed * self._rate.per_second)
            bucket.updated = now
        if bucket.tokens >= 1:
            bucket.tokens -= 1
            return None
        return (1 - bucket.tokens) / self._rate.per_second

    def _make_room(self, now: float) -> None:
        if len(self._buckets) < self._max_keys:
            return
        full = [
            key
            for key, b in self._buckets.items()
            if b.tokens + (now - b.updated) * self._rate.per_second >= self._rate.burst
        ]
        for key in full or [min(self._buckets, key=lambda k: self._buckets[k].updated)]:
            del self._buckets[key]


class RateLimits:
    """The four limiters of ADR 0006 behind one call.

    Args:
        settings: Rates and the on/off switch.
        clock: Monotonic seconds; injected so tests control time.
    """

    def __init__(self, settings: Settings, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._enabled = settings.rate_limit_enabled
        self._limiters: dict[Policy, TokenBucketLimiter] = {
            "ip": TokenBucketLimiter(
                Rate(settings.rate_limit_ip_per_second, settings.rate_limit_ip_burst), clock=clock
            ),
            "principal": TokenBucketLimiter(
                Rate(settings.rate_limit_principal_per_second, settings.rate_limit_principal_burst),
                clock=clock,
            ),
            "login": TokenBucketLimiter(
                Rate(settings.rate_limit_login_per_minute / 60, settings.rate_limit_login_burst),
                clock=clock,
            ),
            "propose": TokenBucketLimiter(
                Rate(
                    settings.rate_limit_propose_per_minute / 60, settings.rate_limit_propose_burst
                ),
                clock=clock,
            ),
        }

    def enforce(self, policy: Policy, key: str) -> None:
        """Spend a token for `key` under `policy`, or raise a 429 that says when to retry."""
        if not self._enabled:
            return
        wait = self._limiters[policy].check(key)
        if wait is None:
            return
        log.warning("rate.limited", policy=policy, key=key)
        raise ApiError(
            429,
            "rate_limited",
            "Too many requests; slow down",
            {"Retry-After": str(max(1, math.ceil(wait)))},
        )


def client_address(request: Request) -> str:
    """The connection's address. Uvicorn already replaced it with `X-Forwarded-For` when the
    connection came from a trusted proxy, so this is safe to use as a limiter key."""
    return request.client.host if request.client else "unknown"


def install_address_rate_limit(app: FastAPI) -> None:
    """Limit every request per client address before authentication or routing work."""

    @app.middleware("http")
    async def address_rate_limit(request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        if request.url.path not in _EXEMPT_PATHS:
            try:
                request.app.state.limits.enforce("ip", client_address(request))
            except ApiError as error:
                return problem(error.status_code, error.code, error.detail, error.headers)
        return await call_next(request)
