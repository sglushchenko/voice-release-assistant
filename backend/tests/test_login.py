import hashlib
import io
import json
import logging
from datetime import UTC, datetime, timedelta

import pytest
import structlog
from fastapi.testclient import TestClient
from pydantic import ValidationError

from release_assistant.api.app import create_app
from release_assistant.api.auth import Principal
from release_assistant.api.login import (
    AuthService,
    LoginThrottle,
    PasswordVerifier,
    TokenStore,
    make_password_hash,
)
from release_assistant.bootstrap import build_mock_application
from release_assistant.config import ApiClient, Settings
from release_assistant.observability.logging import configure_logging

PASSWORD = "correct horse battery staple"
HASH = make_password_hash(PASSWORD)  # argon2 is slow on purpose: hash once per test run


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


def settings(**overrides: object) -> Settings:
    agent = ApiClient(
        subject="agent",
        owner="alice",
        key_sha256=hashlib.sha256(b"agent-key").hexdigest(),
        scopes=frozenset({"read", "propose"}),
    )
    values: dict[str, object] = {
        "admin_username": "alice",
        "admin_password_hash": HASH,
        "api_clients": [agent],
        # High enough that these tests exercise the lockout, not the request rate limit.
        "rate_limits": {"login": "1000/minute"},
        **overrides,
    }
    return Settings(**values)  # type: ignore[arg-type]


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def client(clock: Clock) -> TestClient:
    config = settings()
    return TestClient(
        create_app(config, build_mock_application(), auth_service=AuthService(config, clock=clock))
    )


def login(client: TestClient, password: str = PASSWORD, username: str = "alice"):  # type: ignore[no-untyped-def]
    return client.post("/v1/auth/login", json={"username": username, "password": password})


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_login_returns_a_working_bearer_token(client: TestClient) -> None:
    response = login(client)
    body = response.json()

    assert response.status_code == 200
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 8 * 3600
    assert response.headers["cache-control"] == "no-store"
    me = client.get("/v1/auth/me", headers=bearer(body["access_token"])).json()
    assert me == {
        "subject": "user:alice",
        "owner": "alice",
        "scopes": ["confirm:button", "confirm:voice", "propose", "read"],
    }
    assert client.get("/v1/apps", headers=bearer(body["access_token"])).status_code == 200


def test_wrong_username_and_wrong_password_are_indistinguishable(client: TestClient) -> None:
    wrong_password = login(client, password="not the password")
    wrong_user = login(client, username="mallory")

    assert wrong_password.status_code == wrong_user.status_code == 401
    assert wrong_password.json() == wrong_user.json()
    assert wrong_password.json()["code"] == "invalid_credentials"


def test_login_is_disabled_without_admin_settings(clock: Clock) -> None:
    config = Settings()
    disabled = TestClient(
        create_app(config, build_mock_application(), auth_service=AuthService(config, clock=clock))
    )

    assert login(disabled).status_code == 401


def test_oversized_or_empty_credentials_are_rejected_before_hashing(client: TestClient) -> None:
    assert login(client, password="x" * 1025).status_code == 422
    assert login(client, password="").status_code == 422


def test_token_expires_after_eight_idle_hours_and_use_extends_it(
    client: TestClient, clock: Clock
) -> None:
    token = login(client).json()["access_token"]

    clock.advance(hours=5)
    assert client.get("/v1/auth/me", headers=bearer(token)).status_code == 200
    clock.advance(hours=7)
    assert client.get("/v1/auth/me", headers=bearer(token)).status_code == 200
    clock.advance(hours=8)
    assert client.get("/v1/auth/me", headers=bearer(token)).status_code == 401


def test_logout_revokes_the_token(client: TestClient) -> None:
    token = login(client).json()["access_token"]

    assert client.post("/v1/auth/logout", headers=bearer(token)).status_code == 204
    assert client.get("/v1/auth/me", headers=bearer(token)).status_code == 401


def test_repeated_failures_lock_the_client_out_even_for_the_right_password(
    client: TestClient, clock: Clock
) -> None:
    for _ in range(5):
        assert login(client, password="wrong").status_code == 401

    locked = login(client)
    assert locked.status_code == 429
    assert locked.json()["code"] == "too_many_attempts"
    assert int(locked.headers["retry-after"]) == 15 * 60

    clock.advance(minutes=16)
    assert login(client).status_code == 200


def test_the_human_confirms_what_the_agent_proposed(client: TestClient) -> None:
    human = bearer(login(client).json()["access_token"])
    agent = bearer("agent-key")
    session_id = client.post("/v1/sessions", headers=agent).json()["session_id"]
    proposal = client.post(
        f"/v1/sessions/{session_id}/actions",
        json={
            "action": "add_tester",
            "arguments": {"group_id": "group-1", "email": "new@example.com"},
        },
        headers=agent,
    ).json()["action"]

    by_agent = client.post(
        f"/v1/actions/{proposal['id']}/confirm", json={"channel": "button"}, headers=agent
    )
    by_human = client.post(
        f"/v1/actions/{proposal['id']}/confirm", json={"channel": "button"}, headers=human
    )

    assert by_agent.status_code == 403
    assert by_human.status_code == 200


def test_the_agent_key_is_unaffected_by_logout(client: TestClient) -> None:
    assert client.post("/v1/auth/logout", headers=bearer("agent-key")).status_code == 204
    assert client.get("/v1/apps", headers=bearer("agent-key")).status_code == 200


def test_the_password_never_reaches_the_logs(client: TestClient) -> None:
    stream = io.StringIO()
    configure_logging(Settings(log_json=True), stream=stream)
    try:
        login(client, password="wrong-but-secret")
        login(client)
    finally:
        structlog.reset_defaults()
        logging.getLogger().handlers.clear()

    output = stream.getvalue()
    events = [json.loads(line)["event"] for line in output.splitlines()]
    assert "auth.login_failed" in events
    assert "auth.login_succeeded" in events
    assert "wrong-but-secret" not in output
    assert PASSWORD not in output


def test_misconfiguration_stops_startup() -> None:
    with pytest.raises(ValidationError, match="together"):
        Settings(admin_username="alice")
    with pytest.raises(ValidationError, match="valid argon2 hash"):
        Settings(admin_username="alice", admin_password_hash="plaintext-password")
    stranger = ApiClient(
        subject="agent", owner="bob", key_sha256="0" * 64, scopes=frozenset({"read"})
    )
    with pytest.raises(ValidationError, match="different owner"):
        Settings(admin_username="alice", admin_password_hash=HASH, api_clients=[stranger])


def test_password_verifier_accepts_only_the_exact_pair() -> None:
    verifier = PasswordVerifier("alice", HASH)

    assert verifier.verify("alice", PASSWORD) is True
    assert verifier.verify("alice", PASSWORD + "x") is False
    assert verifier.verify("Alice", PASSWORD) is False


def test_the_hash_is_argon2id_and_salted() -> None:
    assert make_password_hash("a password") != make_password_hash("a password")
    assert HASH.startswith("$argon2id$")


def test_token_store_caps_live_tokens_by_dropping_the_least_recently_used() -> None:
    clock = Clock()
    store = TokenStore(timedelta(hours=8), max_tokens=2, clock=clock)
    who = Principal("user:alice", "alice", frozenset({"read"}))
    first = store.issue(who)
    clock.advance(minutes=1)
    second = store.issue(who)
    clock.advance(minutes=1)
    store.lookup(first)
    clock.advance(minutes=1)
    third = store.issue(who)

    assert store.lookup(first) is who
    assert store.lookup(third) is who
    assert store.lookup(second) is None


def test_throttle_is_per_client_and_forgets_old_failures() -> None:
    clock = Clock()
    throttle = LoginThrottle(max_failures=3, lockout=timedelta(minutes=10), clock=clock)
    for _ in range(3):
        throttle.record_failure("1.1.1.1")

    assert throttle.retry_after("1.1.1.1") is not None
    assert throttle.retry_after("2.2.2.2") is None

    slow = LoginThrottle(max_failures=3, lockout=timedelta(minutes=10), clock=clock)
    for _ in range(5):
        slow.record_failure("3.3.3.3")
        clock.advance(minutes=11)
    assert slow.retry_after("3.3.3.3") is None


def test_a_successful_login_clears_earlier_failures() -> None:
    throttle = LoginThrottle(max_failures=3, clock=Clock())
    throttle.record_failure("1.1.1.1")
    throttle.record_failure("1.1.1.1")
    throttle.record_success("1.1.1.1")
    throttle.record_failure("1.1.1.1")
    throttle.record_failure("1.1.1.1")

    assert throttle.retry_after("1.1.1.1") is None
