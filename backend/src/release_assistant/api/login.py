"""Password login, session tokens and brute-force protection (ADR 0005)."""

import asyncio
import hashlib
import hmac
import math
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from release_assistant.api.auth import Authenticator, Principal
from release_assistant.api.errors import ApiError
from release_assistant.config import Settings

log = structlog.get_logger(__name__)

HUMAN_SCOPES = frozenset({"read", "propose", "confirm:button", "confirm:voice"})

# Argon2 is deliberately memory-hungry; this bounds how many verifications run at once.
_MAX_CONCURRENT_VERIFICATIONS = 2


def _utcnow() -> datetime:
    return datetime.now(UTC)


def make_password_hash(password: str) -> str:
    """argon2id hash with the library's recommended parameters, safe to put in configuration."""
    return PasswordHasher().hash(password)


class PasswordVerifier:
    """Checks a username and password against the configured account.

    Both are always evaluated, so a wrong username and a wrong password cost the same time
    and look the same.
    """

    def __init__(self, username: str, password_hash: str) -> None:
        self._username = username
        self._hash = password_hash
        self._hasher = PasswordHasher()

    def verify(self, username: str, password: str) -> bool:
        user_ok = hmac.compare_digest(username.encode(), self._username.encode())
        try:
            self._hasher.verify(self._hash, password)
            password_ok = True
        except (VerificationError, InvalidHashError):
            password_ok = False
        return user_ok and password_ok


@dataclass
class _Session:
    principal: Principal
    last_used: datetime


class TokenStore:
    """Opaque session tokens kept only in memory, so a restart revokes them all.

    Args:
        ttl: Idle time after which a token expires; each lookup extends it.
        max_tokens: Live tokens kept; the least recently used is dropped beyond this.
        clock: Injected so tests control expiry.
    """

    def __init__(
        self,
        ttl: timedelta,
        *,
        max_tokens: int = 20,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._ttl = ttl
        self._max_tokens = max_tokens
        self._clock = clock
        self._sessions: dict[str, _Session] = {}

    @property
    def ttl(self) -> timedelta:
        return self._ttl

    @staticmethod
    def _key(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def issue(self, principal: Principal) -> str:
        now = self._clock()
        self._prune(now)
        while len(self._sessions) >= self._max_tokens:
            oldest = min(self._sessions, key=lambda k: self._sessions[k].last_used)
            del self._sessions[oldest]
        token = secrets.token_urlsafe(32)
        self._sessions[self._key(token)] = _Session(principal, now)
        return token

    def lookup(self, token: str) -> Principal | None:
        """The principal for a live token, which counts as used now."""
        now = self._clock()
        session = self._sessions.get(self._key(token))
        if session is None or now - session.last_used >= self._ttl:
            return None
        session.last_used = now
        return session.principal

    def revoke(self, token: str) -> bool:
        return self._sessions.pop(self._key(token), None) is not None

    def _prune(self, now: datetime) -> None:
        stale = [k for k, s in self._sessions.items() if now - s.last_used >= self._ttl]
        for key in stale:
            del self._sessions[key]


@dataclass
class _Attempts:
    failures: int
    first_failure: datetime
    locked_until: datetime | None = None


class LoginThrottle:
    """Locks a client address out after repeated failed logins.

    Failures older than the lockout period are forgotten, so a slow trickle never locks
    anyone out. State is in memory and bounded.

    Args:
        max_failures: Failures that trigger a lockout.
        lockout: Lockout length, and also the window failures are counted in.
        clock: Injected so tests control time.
    """

    _MAX_TRACKED = 10_000

    def __init__(
        self,
        *,
        max_failures: int = 5,
        lockout: timedelta = timedelta(minutes=15),
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._max_failures = max_failures
        self._lockout = lockout
        self._clock = clock
        self._attempts: dict[str, _Attempts] = {}

    def retry_after(self, client: str) -> timedelta | None:
        """Time left on a lockout, or None if the client may try."""
        state = self._attempts.get(client)
        if state is None or state.locked_until is None:
            return None
        remaining = state.locked_until - self._clock()
        if remaining <= timedelta(0):
            del self._attempts[client]
            return None
        return remaining

    def record_failure(self, client: str) -> None:
        now = self._clock()
        state = self._attempts.get(client)
        if state is None or now - state.first_failure >= self._lockout:
            self._make_room(now)
            state = _Attempts(failures=0, first_failure=now)
            self._attempts[client] = state
        state.failures += 1
        if state.failures >= self._max_failures:
            state.locked_until = now + self._lockout

    def record_success(self, client: str) -> None:
        self._attempts.pop(client, None)

    def _make_room(self, now: datetime) -> None:
        if len(self._attempts) < self._MAX_TRACKED:
            return
        expired = [c for c, s in self._attempts.items() if now - s.first_failure >= self._lockout]
        for client in expired or [
            min(self._attempts, key=lambda c: self._attempts[c].first_failure)
        ]:
            del self._attempts[client]


@dataclass(frozen=True)
class LoginResult:
    access_token: str
    expires_in: int


class AuthService:
    """Everything that decides who a request is: static keys, login tokens and login itself.

    Args:
        settings: Credentials and limits.
        clock: Injected so tests control token expiry and lockouts.
    """

    def __init__(self, settings: Settings, *, clock: Callable[[], datetime] = _utcnow) -> None:
        self._keys = Authenticator(settings.api_clients)
        self._owner = settings.admin_username
        self._verifier = (
            PasswordVerifier(
                settings.admin_username, settings.admin_password_hash.get_secret_value()
            )
            if settings.admin_username and settings.admin_password_hash
            else None
        )
        self._tokens = TokenStore(timedelta(minutes=settings.access_token_ttl_minutes), clock=clock)
        self._throttle = LoginThrottle(
            max_failures=settings.login_max_failures,
            lockout=timedelta(minutes=settings.login_lockout_minutes),
            clock=clock,
        )
        self._slots = asyncio.Semaphore(_MAX_CONCURRENT_VERIFICATIONS)

    @property
    def login_enabled(self) -> bool:
        return self._verifier is not None

    def authenticate(self, token: str) -> Principal | None:
        """A login token or a static API key, whichever the bearer token is."""
        return self._tokens.lookup(token) or self._keys.authenticate(token)

    async def login(self, username: str, password: str, client: str) -> LoginResult:
        """Exchange a username and password for a session token.

        Raises ApiError 401 for any wrong credential, alike for every cause, and 429 while
        `client` is locked out.
        """
        wait = self._throttle.retry_after(client)
        if wait is not None:
            log.warning("auth.login_locked", client=client)
            raise ApiError(
                429,
                "too_many_attempts",
                "Too many failed attempts; try again later",
                {"Retry-After": str(math.ceil(wait.total_seconds()))},
            )
        accepted = False
        if self._verifier is not None:
            async with self._slots:
                accepted = await asyncio.to_thread(self._verifier.verify, username, password)
        if not accepted or self._owner is None:
            self._throttle.record_failure(client)
            log.warning("auth.login_failed", client=client, login_enabled=self.login_enabled)
            raise ApiError(401, "invalid_credentials", "Invalid username or password")
        self._throttle.record_success(client)
        principal = Principal(subject=f"user:{self._owner}", owner=self._owner, scopes=HUMAN_SCOPES)
        token = self._tokens.issue(principal)
        log.info("auth.login_succeeded", client=client)
        return LoginResult(token, int(self._tokens.ttl.total_seconds()))

    def logout(self, token: str) -> None:
        """Revoke a login token. Static API keys are not revocable this way."""
        if self._tokens.revoke(token):
            log.info("auth.logout")
