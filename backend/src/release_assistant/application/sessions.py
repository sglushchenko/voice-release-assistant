"""Server-side sessions: conversation state the client can neither forge nor reset.

Trust state (ADR 0003) lives in a session, so it must be stored by the server, keyed by an
unguessable id and tied to the account that created it. A session that is idle too long
expires, and each account may hold only a limited number, which bounds memory.
"""

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog

from release_assistant.application.errors import TooManySessionsError
from release_assistant.domain.errors import NotFoundError

log = structlog.get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class _Entry[T]:
    owner: str
    value: T
    last_used: datetime


class SessionRegistry[T]:
    """In-memory registry; single process only. A shared store replaces it when scaling out.

    Args:
        factory: Builds the session object for an owner.
        ttl: Idle time after which a session expires.
        max_per_owner: Live sessions one owner may hold.
        clock: Injected so tests control expiry.
    """

    def __init__(
        self,
        factory: Callable[[str], T],
        *,
        ttl: timedelta = timedelta(minutes=30),
        max_per_owner: int = 20,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._factory = factory
        self._ttl = ttl
        self._max_per_owner = max_per_owner
        self._clock = clock
        self._entries: dict[str, _Entry[T]] = {}

    def create(self, owner: str) -> tuple[str, T]:
        """Start a session; returns its id and the session object."""
        now = self._clock()
        self._prune(now)
        if sum(1 for e in self._entries.values() if e.owner == owner) >= self._max_per_owner:
            raise TooManySessionsError(f"Limit of {self._max_per_owner} sessions reached")
        session_id = secrets.token_urlsafe(24)
        value = self._factory(owner)
        self._entries[session_id] = _Entry(owner, value, now)
        log.info("session.created", session_id=session_id)
        return session_id, value

    def get(self, session_id: str, owner: str) -> T:
        """The owner's live session, which counts as used now. Anything else, whether unknown,
        expired or someone else's, is reported alike as not found."""
        now = self._clock()
        self._prune(now)
        entry = self._entries.get(session_id)
        if entry is None or entry.owner != owner:
            raise NotFoundError(f"Session {session_id!r} not found")
        entry.last_used = now
        return entry.value

    def _prune(self, now: datetime) -> None:
        expired = [sid for sid, e in self._entries.items() if now - e.last_used >= self._ttl]
        for sid in expired:
            del self._entries[sid]
            log.info("session.expired", session_id=sid)
