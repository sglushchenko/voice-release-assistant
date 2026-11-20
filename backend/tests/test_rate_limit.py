import hashlib

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from release_assistant.api.app import create_app
from release_assistant.api.errors import ApiError
from release_assistant.api.ratelimit import RateLimits
from release_assistant.bootstrap import build_mock_application
from release_assistant.config import RATE_LIMIT_DEFAULTS, ApiClient, Scope, Settings


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
        **overrides,
    }
    settings = Settings(**values)  # type: ignore[arg-type]
    return TestClient(create_app(settings, build_mock_application()))


# --- the policy layer on its own -------------------------------------------------------


async def test_a_policy_blocks_once_its_limit_is_used_and_says_when_to_retry() -> None:
    limits = RateLimits(Settings(rate_limits={"login": "2/minute"}))

    await limits.enforce("login", "1.1.1.1")
    await limits.enforce("login", "1.1.1.1")
    with pytest.raises(ApiError) as blocked:
        await limits.enforce("login", "1.1.1.1")

    assert blocked.value.status_code == 429
    assert blocked.value.code == "rate_limited"
    assert 1 <= int((blocked.value.headers or {})["Retry-After"]) <= 60


async def test_keys_and_policies_are_counted_separately() -> None:
    limits = RateLimits(Settings(rate_limits={"login": "1/minute", "propose": "1/minute"}))

    await limits.enforce("login", "a")
    await limits.enforce("login", "b")
    await limits.enforce("propose", "a")
    with pytest.raises(ApiError):
        await limits.enforce("login", "a")


async def test_a_policy_can_combine_several_limits() -> None:
    limits = RateLimits(Settings(rate_limits={"login": "10/second;2/minute"}))

    await limits.enforce("login", "k")
    await limits.enforce("login", "k")
    with pytest.raises(ApiError):
        await limits.enforce("login", "k")


async def test_disabled_limits_never_block() -> None:
    limits = RateLimits(Settings(rate_limit_enabled=False, rate_limits={"login": "1/minute"}))

    for _ in range(20):
        await limits.enforce("login", "k")


def test_overrides_replace_only_the_named_policies() -> None:
    effective = Settings(rate_limits={"login": "3/minute"}).effective_rate_limits

    assert effective["login"] == "3/minute"
    assert effective["reports"] == RATE_LIMIT_DEFAULTS["reports"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"rate_limits": {"login": "three per minute"}},
        {"rate_limits": {"no_such_policy": "3/minute"}},
        {"rate_limit_storage": "memory://"},
    ],
)
def test_bad_rate_limit_configuration_stops_startup(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(**overrides)  # type: ignore[arg-type]


# --- limits applied to routes ----------------------------------------------------------


def test_the_address_limit_returns_429_with_retry_after_and_a_request_id() -> None:
    client = make_client(rate_limits={"ip": "3/minute"})

    codes = [client.get("/v1/apps", headers=bearer("agent-key")).status_code for _ in range(4)]
    blocked = client.get("/v1/apps", headers=bearer("agent-key"))

    assert codes == [200, 200, 200, 429]
    assert blocked.headers["content-type"] == "application/problem+json"
    assert blocked.json()["code"] == "rate_limited"
    assert int(blocked.headers["retry-after"]) >= 1
    assert blocked.headers["x-request-id"]


def test_the_address_limit_applies_before_authentication() -> None:
    client = make_client(rate_limits={"ip": "2/minute"})

    statuses = [client.get("/v1/apps").status_code for _ in range(3)]

    assert statuses == [401, 401, 429]


def test_the_liveness_probe_is_never_limited() -> None:
    client = make_client(rate_limits={"ip": "1/minute"})

    assert {client.get("/healthz").status_code for _ in range(20)} == {200}


def test_principals_are_limited_separately() -> None:
    client = make_client(rate_limits={"principal": "2/minute"})

    agent = [client.get("/v1/apps", headers=bearer("agent-key")).status_code for _ in range(3)]
    human = client.get("/v1/apps", headers=bearer("human-key")).status_code

    assert agent == [200, 200, 429]
    assert human == 200


def test_login_attempts_are_limited_before_any_password_is_checked() -> None:
    client = make_client(rate_limits={"login": "2/minute"})
    body = {"username": "alice", "password": "whatever"}

    statuses = [client.post("/v1/auth/login", json=body).status_code for _ in range(3)]

    assert statuses == [401, 401, 429]
    assert client.post("/v1/auth/login", json=body).json()["code"] == "rate_limited"


def test_reports_have_their_own_limit_that_leaves_other_routes_alone() -> None:
    client = make_client(rate_limits={"reports": "2/minute"})
    agent = bearer("agent-key")
    params = {"year": 2026, "month": 10}

    reports = [
        client.get("/v1/reports/sales/monthly", params=params, headers=agent).status_code
        for _ in range(3)
    ]

    assert reports == [200, 200, 429]
    assert client.get("/v1/apps", headers=agent).status_code == 200


def test_every_report_route_shares_the_reports_limit() -> None:
    client = make_client(rate_limits={"reports": "2/minute"})
    agent = bearer("agent-key")

    first = client.get(
        "/v1/reports/sales/monthly", params={"year": 2026, "month": 10}, headers=agent
    )
    second = client.get("/v1/reports/engagement/recent", headers=agent)
    third = client.get(
        "/v1/reports/engagement", params={"start": "2026-11-01", "end": "2026-11-14"}, headers=agent
    )

    assert (first.status_code, second.status_code, third.status_code) == (200, 200, 429)


def test_proposals_have_their_own_limit() -> None:
    client = make_client(rate_limits={"propose": "2/minute"})
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


def test_review_reads_have_their_own_limit() -> None:
    client = make_client(rate_limits={"reviews": "2/minute"})
    agent = bearer("agent-key")
    session_id = client.post("/v1/sessions", headers=agent).json()["session_id"]
    url = f"/v1/sessions/{session_id}/apps/app-1/reviews"

    statuses = [client.get(url, headers=agent).status_code for _ in range(3)]

    assert statuses == [200, 200, 429]


def test_limits_can_be_switched_off() -> None:
    client = make_client(rate_limit_enabled=False, rate_limits={"ip": "1/minute"})

    assert {client.get("/v1/apps", headers=bearer("agent-key")).status_code for _ in range(10)} == {
        200
    }


# --- request size cap ------------------------------------------------------------------


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
