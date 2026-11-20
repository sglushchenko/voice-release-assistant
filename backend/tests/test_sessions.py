from datetime import UTC, datetime, timedelta

import pytest

from release_assistant.application.errors import TooManySessionsError
from release_assistant.application.sessions import SessionRegistry
from release_assistant.domain.errors import NotFoundError


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def registry(clock: Clock, **kwargs: int) -> SessionRegistry[dict[str, str]]:
    return SessionRegistry(
        lambda owner: {"owner": owner},
        ttl=timedelta(minutes=30),
        clock=clock,
        **kwargs,
    )


def test_create_returns_unguessable_distinct_ids_and_the_session() -> None:
    sessions = registry(Clock())

    first_id, first = sessions.create("alice")
    second_id, _ = sessions.create("alice")

    assert first == {"owner": "alice"}
    assert first_id != second_id
    assert len(first_id) >= 32


def test_only_the_owner_can_get_a_session_and_others_see_not_found() -> None:
    sessions = registry(Clock())
    session_id, session = sessions.create("alice")

    assert sessions.get(session_id, "alice") is session
    with pytest.raises(NotFoundError):
        sessions.get(session_id, "bob")
    with pytest.raises(NotFoundError):
        sessions.get("unknown", "alice")


def test_idle_sessions_expire_but_use_keeps_them_alive() -> None:
    clock = Clock()
    sessions = registry(clock)
    kept_id, _ = sessions.create("alice")
    idle_id, _ = sessions.create("alice")

    clock.now += timedelta(minutes=20)
    sessions.get(kept_id, "alice")
    clock.now += timedelta(minutes=20)

    sessions.get(kept_id, "alice")
    with pytest.raises(NotFoundError):
        sessions.get(idle_id, "alice")


def test_owner_limit_is_enforced_per_owner_and_freed_by_expiry() -> None:
    clock = Clock()
    sessions = registry(clock, max_per_owner=2)
    sessions.create("alice")
    sessions.create("alice")

    with pytest.raises(TooManySessionsError):
        sessions.create("alice")
    sessions.create("bob")

    clock.now += timedelta(minutes=31)
    sessions.create("alice")
