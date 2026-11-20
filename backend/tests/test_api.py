import hashlib
import io
import json
import logging

import pytest
import structlog
from fastapi.testclient import TestClient

from release_assistant.api.app import create_app
from release_assistant.bootstrap import build_mock_application
from release_assistant.config import ApiClient, Settings
from release_assistant.observability.logging import configure_logging


def sha256(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


READER = {"Authorization": "Bearer reader-key"}
NO_SCOPES = {"Authorization": "Bearer powerless-key"}


@pytest.fixture
def client() -> TestClient:
    settings = Settings(
        api_clients=[
            ApiClient(
                subject="dashboard", owner="alice", key_sha256=sha256("reader-key"), scopes={"read"}
            ),
            ApiClient(
                subject="agent",
                owner="alice",
                key_sha256=sha256("powerless-key"),
                scopes={"propose"},
            ),
        ]
    )
    return TestClient(create_app(settings, build_mock_application()))


def test_health_is_open(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic abc"}]
)
def test_missing_or_invalid_credentials_get_401(
    client: TestClient, headers: dict[str, str]
) -> None:
    response = client.get("/v1/apps", headers=headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "unauthorized"


def test_valid_key_without_the_scope_gets_403(client: TestClient) -> None:
    response = client.get("/v1/apps", headers=NO_SCOPES)

    assert response.status_code == 403
    assert response.json()["detail"] == "Missing scope: read"


def test_no_configured_clients_rejects_everything() -> None:
    closed = TestClient(create_app(Settings(api_clients=[]), build_mock_application()))

    assert closed.get("/v1/apps", headers=READER).status_code == 401


def test_list_and_find_apps(client: TestClient) -> None:
    everything = client.get("/v1/apps", headers=READER).json()
    found = client.get("/v1/apps", params={"name": "habit"}, headers=READER).json()

    assert [a["id"] for a in everything] == ["app-1", "app-2"]
    assert [a["id"] for a in found] == ["app-2"]


def test_versions_filtered_by_state_and_review_status(client: TestClient) -> None:
    versions = client.get(
        "/v1/apps/app-2/versions", params={"state": "REJECTED"}, headers=READER
    ).json()
    in_review = client.get("/v1/versions/in-review", headers=READER).json()

    assert [v["id"] for v in versions] == ["ver-3"]
    assert in_review[0]["app"]["name"] == "Pocket Notes"


def test_builds_list_limit_and_latest(client: TestClient) -> None:
    limited = client.get("/v1/apps/app-1/builds", params={"limit": 1}, headers=READER).json()
    latest = client.get("/v1/apps/app-1/builds/latest", headers=READER).json()

    assert [b["build_number"] for b in limited] == ["42"]
    assert latest["build_number"] == "42"


def test_unknown_app_is_a_404_problem(client: TestClient) -> None:
    response = client.get("/v1/apps/missing/builds", headers=READER)

    assert response.status_code == 404
    assert response.json() == {
        "title": "Not Found",
        "status": 404,
        "code": "not_found",
        "detail": "App 'missing' not found",
    }


def test_invalid_query_is_a_422_that_does_not_echo_input(client: TestClient) -> None:
    response = client.get("/v1/apps/app-1/builds", params={"limit": "0"}, headers=READER)

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert "query.limit" in response.json()["detail"]


def test_beta_groups_and_testers(client: TestClient) -> None:
    groups = client.get("/v1/apps/app-1/beta-groups", headers=READER).json()
    testers = client.get("/v1/beta-groups/group-2/testers", headers=READER).json()

    assert [g["name"] for g in groups] == ["Internal", "Public Beta"]
    assert [t["email"] for t in testers] == ["anna@example.com", "boris@example.com"]


def test_monthly_sales_report(client: TestClient) -> None:
    response = client.get(
        "/v1/reports/sales/monthly", params={"year": 2026, "month": 10}, headers=READER
    )

    body = response.json()
    assert response.status_code == 200
    assert body["current"]["downloads"] == 200
    assert body["current"]["proceeds"]["USD"] == "55.00"


def test_month_out_of_range_is_rejected(client: TestClient) -> None:
    response = client.get(
        "/v1/reports/sales/monthly", params={"year": 2026, "month": 13}, headers=READER
    )

    assert response.status_code == 422


def test_engagement_report_exposes_completeness(client: TestClient) -> None:
    ok = client.get(
        "/v1/reports/engagement",
        params={"start": "2026-11-01", "end": "2026-11-14"},
        headers=READER,
    ).json()
    beyond = client.get(
        "/v1/reports/engagement",
        params={"start": "2026-11-10", "end": "2026-11-20"},
        headers=READER,
    ).json()

    assert ok["is_complete"] is True
    assert ok["totals"]["impressions"] == 22000
    assert beyond["is_complete"] is False


def test_engagement_period_validation(client: TestClient) -> None:
    backwards = client.get(
        "/v1/reports/engagement",
        params={"start": "2026-11-14", "end": "2026-11-01"},
        headers=READER,
    )
    too_long = client.get(
        "/v1/reports/engagement",
        params={"start": "2024-01-01", "end": "2026-01-01"},
        headers=READER,
    )

    assert backwards.status_code == too_long.status_code == 422
    assert backwards.json()["code"] == "invalid_period"


def test_recent_engagement_ends_at_latest_published_day(client: TestClient) -> None:
    body = client.get("/v1/reports/engagement/recent", params={"days": 7}, headers=READER).json()

    assert (body["start"], body["end"]) == ("2026-11-12", "2026-11-18")


def test_request_id_is_generated_and_echoed(client: TestClient) -> None:
    generated = client.get("/healthz").headers["x-request-id"]
    echoed = client.get("/healthz", headers={"X-Request-ID": "trace-123"}).headers["x-request-id"]
    forged = client.get("/healthz", headers={"X-Request-ID": "bad id\nfake log line"}).headers[
        "x-request-id"
    ]

    assert len(generated) == 32
    assert echoed == "trace-123"
    assert forged != "bad id\nfake log line"


def test_access_log_carries_request_id_and_subject_but_no_token(client: TestClient) -> None:
    stream = io.StringIO()
    configure_logging(Settings(log_json=True), stream=stream)
    try:
        client.get("/v1/apps", headers=READER)
    finally:
        structlog.reset_defaults()
        logging.getLogger().handlers.clear()

    entries = [json.loads(line) for line in stream.getvalue().splitlines()]
    [access] = [e for e in entries if e["event"] == "http.request"]
    assert access["subject"] == "dashboard"
    assert access["status"] == 200
    assert len(access["request_id"]) == 32
    assert "reader-key" not in stream.getvalue()


def test_unexpected_errors_return_a_generic_500() -> None:
    application = build_mock_application()

    async def boom() -> None:
        raise RuntimeError("secret internals")

    application.apps.list_apps = boom  # type: ignore[method-assign]
    settings = Settings(
        api_clients=[
            ApiClient(subject="d", owner="alice", key_sha256=sha256("reader-key"), scopes={"read"})
        ]
    )
    tolerant = TestClient(create_app(settings, application), raise_server_exceptions=False)

    response = tolerant.get("/v1/apps", headers=READER)

    assert response.status_code == 500
    assert response.json()["detail"] == "Internal server error"
    assert "secret" not in response.text
