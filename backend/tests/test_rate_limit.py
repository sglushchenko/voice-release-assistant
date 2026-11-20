import hashlib

import pytest
from fastapi.testclient import TestClient

from release_assistant.api.app import create_app
from release_assistant.api.ratelimit import Rate, RateLimits, TokenBucketLimiter
from release_assistant.bootstrap import build_mock_application
from release_assistant.config import ApiClient, Scope, Settings

# Refill so slow that tests never see a token come back by themselves.
SLOW = 0.0001


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def api_client(subject: str, key: str, *scopes: Scope) -> ApiClient:
    return ApiClient(
        subject=subject,
        owner="alice",
        key_sha256=hashlib.sha256(key.encode()).hexdigest(),
        scopes=frozenset(scopes),
    )


def make_client(**overrides: object) -> TestClient:
    values: dict[str, object] = {
        "api_clients": [
            api_client("agent", "agent-key", "read", "propose"),
            api_client("human", "human-key", "read", "propose"),
        ],
        # Everything generous unless a test narrows the limit it is about.
        "rate_limit_ip_per_second": 1000,
        "rate_limit_ip_burst": 1000,
        "rate_limit_principal_per_second": 1000,
        "rate_limit_principal_burst": 1000,
        **overrides,
    }
    settings = Settings(**values)  # type: ignore[arg-type]
    return TestClient(create_app(settings, build_mock_application()))


def test_bucket_allows_a_burst_then_says_how_long_to_wait() -> None:
    limiter = TokenBucketLimiter(Rate(per_second=1, burst=3), clock=Clock())

    assert [limiter.check("k") for _ in range(3)] == [None, None, None]
    assert limiter.check("k") == pytest.approx(1.0)


def test_bucket_refills_with_time_but_never_beyond_the_burst() -> None:
    clock = Clock()
    limiter = TokenBucketLimiter(Rate(per_second=1, burst=2), clock=clock)
    limiter.check("k")
    limiter.check("k")
    assert limiter.check("k") is not None

    clock.now += 1
    assert limiter.check("k") is None
    assert limiter.check("k") is not None

    clock.now += 3600
    assert [limiter.check("k") for _ in range(3)] == [None, None, pytest.approx(1.0)]


def test_buckets_are_independent_per_key() -> None:
    limiter = TokenBucketLimiter(Rate(per_second=SLOW, burst=1), clock=Clock())

    assert limiter.check("a") is None
    assert limiter.check("a") is not None
    assert limiter.check("b") is None


def test_the_number_of_tracked_keys_stays_bounded() -> None:
    clock = Clock()
    limiter = TokenBucketLimiter(Rate(per_second=1, burst=1), clock=clock, max_keys=3)

    for key in ("a", "b", "c", "d", "e"):
        limiter.check(key)
        clock.now += 0.1

    assert len(limiter._buckets) <= 3


@pytest.mark.parametrize(("per_second", "burst"), [(0, 1), (-1, 1), (1, 0)])
def test_invalid_rates_are_rejected(per_second: float, burst: int) -> None:
    with pytest.raises(ValueError, match="rate"):
        Rate(per_second, burst)


def test_disabled_limits_never_block() -> None:
    limits = RateLimits(Settings(rate_limit_enabled=False, rate_limit_login_burst=1))

    for _ in range(50):
        limits.enforce("login", "1.1.1.1")


def test_the_address_limit_returns_429_with_retry_after_and_a_request_id() -> None:
    client = make_client(rate_limit_ip_burst=3, rate_limit_ip_per_second=SLOW)

    codes = [client.get("/v1/apps", headers=bearer("agent-key")).status_code for _ in range(4)]
    blocked = client.get("/v1/apps", headers=bearer("agent-key"))

    assert codes == [200, 200, 200, 429]
    assert blocked.headers["content-type"] == "application/problem+json"
    assert blocked.json()["code"] == "rate_limited"
    assert int(blocked.headers["retry-after"]) >= 1
    assert blocked.headers["x-request-id"]


def test_the_address_limit_applies_before_authentication() -> None:
    client = make_client(rate_limit_ip_burst=2, rate_limit_ip_per_second=SLOW)

    statuses = [client.get("/v1/apps").status_code for _ in range(3)]

    assert statuses == [401, 401, 429]


def test_the_liveness_probe_is_never_limited() -> None:
    client = make_client(rate_limit_ip_burst=1, rate_limit_ip_per_second=SLOW)

    assert {client.get("/healthz").status_code for _ in range(20)} == {200}


def test_principals_are_limited_separately() -> None:
    client = make_client(rate_limit_principal_burst=2, rate_limit_principal_per_second=SLOW)

    agent = [client.get("/v1/apps", headers=bearer("agent-key")).status_code for _ in range(3)]
    human = client.get("/v1/apps", headers=bearer("human-key")).status_code

    assert agent == [200, 200, 429]
    assert human == 200


def test_login_attempts_are_limited_before_any_password_is_checked() -> None:
    client = make_client(rate_limit_login_burst=2, rate_limit_login_per_minute=0.01)
    body = {"username": "alice", "password": "whatever"}

    statuses = [client.post("/v1/auth/login", json=body).status_code for _ in range(3)]
    third = client.post("/v1/auth/login", json=body)

    assert statuses == [401, 401, 429]
    assert third.json()["code"] == "rate_limited"


def test_proposals_have_their_own_tighter_limit() -> None:
    client = make_client(rate_limit_propose_burst=2, rate_limit_propose_per_minute=0.01)
    agent = bearer("agent-key")
    session_id = client.post("/v1/sessions", headers=agent).json()["session_id"]
    body = {
        "action": "add_tester",
        "arguments": {"group_id": "group-1", "email": "new@example.com"},
    }

    statuses = [
        client.post(f"/v1/sessions/{session_id}/actions", json=body, headers=agent).status_code
        for _ in range(3)
    ]

    assert statuses == [202, 202, 429]
    assert client.get("/v1/apps", headers=agent).status_code == 200


def test_limits_can_be_switched_off() -> None:
    client = make_client(
        rate_limit_enabled=False, rate_limit_ip_burst=1, rate_limit_ip_per_second=SLOW
    )

    assert {client.get("/v1/apps", headers=bearer("agent-key")).status_code for _ in range(10)} == {
        200
    }


def test_a_declared_oversized_body_is_refused() -> None:
    client = make_client(max_request_bytes=1024)
    agent = bearer("agent-key")
    session_id = client.post("/v1/sessions", headers=agent).json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/actions",
        content=b"x" * 2048,
        headers={**agent, "Content-Type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json()["code"] == "payload_too_large"
    assert response.headers["x-request-id"]


def test_a_streamed_body_without_a_length_is_cut_off_too() -> None:
    client = make_client(max_request_bytes=1024)
    agent = bearer("agent-key")
    session_id = client.post("/v1/sessions", headers=agent).json()["session_id"]

    def chunks():  # type: ignore[no-untyped-def]
        for _ in range(10):
            yield b"x" * 512

    response = client.post(
        f"/v1/sessions/{session_id}/actions",
        content=chunks(),
        headers={**agent, "Content-Type": "application/json"},
    )

    assert response.status_code == 413


def test_a_body_within_the_limit_is_processed_normally() -> None:
    client = make_client(max_request_bytes=1024)
    agent = bearer("agent-key")
    session_id = client.post("/v1/sessions", headers=agent).json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/actions",
        json={"action": "add_tester", "arguments": {"group_id": "group-1", "email": "a@b.co"}},
        headers=agent,
    )

    assert response.status_code == 202
