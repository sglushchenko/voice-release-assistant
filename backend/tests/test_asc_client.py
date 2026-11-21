import json

import httpx
import jwt
import pytest
import structlog

from asc_support import APPS, BASE_URL, make_client, page, public_pem, resource
from release_assistant.domain.errors import NotFoundError, UpstreamError
from release_assistant.infrastructure.appstoreconnect.client import AscClient


def seen_into(requests: list[httpx.Request], answer: httpx.Response | None = None):  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return answer or httpx.Response(200, json=APPS)

    return handler


def problem(status: int, code: str = "X", title: str = "T") -> httpx.Response:
    return httpx.Response(
        status, json={"errors": [{"status": str(status), "code": code, "title": title}]}
    )


# --- requests --------------------------------------------------------------------------


async def test_every_request_carries_a_signed_bearer_token_to_apple() -> None:
    requests: list[httpx.Request] = []
    client = make_client(seen_into(requests))

    await client.get("/v1/apps/1001", {"fields[apps]": "name"})

    request = requests[0]
    assert str(request.url).startswith(f"{BASE_URL}/v1/apps/1001")
    assert request.url.params["fields[apps]"] == "name"
    token = request.headers["Authorization"].removeprefix("Bearer ")
    assert jwt.get_unverified_header(token)["alg"] == "ES256"


# --- pagination ------------------------------------------------------------------------


async def test_lists_follow_the_next_link_and_merge_data_and_included() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if "cursor" in request.url.params:
            return httpx.Response(
                200, json=page([resource("apps", "2", {})], included=[{"type": "x", "id": "b"}])
            )
        return httpx.Response(
            200,
            json=page(
                [resource("apps", "1", {})],
                included=[{"type": "x", "id": "a"}],
                next_path="/v1/apps?cursor=abc",
            ),
        )

    data, included = await make_client(handler).get_all("/v1/apps", {"limit": 200})

    assert [d["id"] for d in data] == ["1", "2"]
    assert [i["id"] for i in included] == ["a", "b"]
    assert requests[0].url.params["limit"] == "200"
    assert requests[1].url.params["cursor"] == "abc"


async def test_a_list_longer_than_the_page_limit_is_cut_and_the_cut_is_logged() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=page([resource("apps", "n", {})], next_path="/v1/apps?cursor=more")
        )

    client = make_client(handler, max_pages=3)

    with structlog.testing.capture_logs() as logs:
        data, _ = await client.get_all("/v1/apps")

    assert len(data) == 3
    assert any(entry["event"] == "asc.list_truncated" for entry in logs)


async def test_a_next_link_to_another_host_is_refused_and_never_followed() -> None:
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        document = page([resource("apps", "1", {})])
        document["links"]["next"] = "https://evil.example/steal?token=1"
        return httpx.Response(200, json=document)

    with pytest.raises(UpstreamError, match="another host"):
        await make_client(handler).get_all("/v1/apps")

    assert hosts == ["api.appstoreconnect.apple.com"]  # the token never went to evil.example


# --- retries ---------------------------------------------------------------------------


async def test_a_rate_limited_request_waits_as_told_and_then_succeeds() -> None:
    answers = iter([problem(429, "RATE_LIMIT_EXCEEDED"), httpx.Response(200, json=APPS)])
    slept: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        response = next(answers)
        if response.status_code == 429:
            return httpx.Response(
                429, headers={"Retry-After": "2"}, json=json.loads(response.content)
            )
        return response

    async def record(seconds: float) -> None:
        slept.append(seconds)

    document = await make_client(handler, sleep=record).get("/v1/apps")

    assert document == APPS
    assert slept == [2.0]


async def test_server_errors_back_off_exponentially_then_give_up_with_a_safe_message() -> None:
    slept: list[float] = []
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return problem(503, "UNAVAILABLE", "Service Unavailable")

    async def record(seconds: float) -> None:
        slept.append(seconds)

    with pytest.raises(UpstreamError, match="error 503: Service Unavailable"):
        await make_client(handler, sleep=record, max_retries=3).get("/v1/apps")

    assert calls == 4
    assert slept == [0.5, 1.0, 2.0]


async def test_a_dropped_connection_is_retried_then_reported_calmly() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused to 10.0.0.1")

    with pytest.raises(UpstreamError) as raised:
        await make_client(handler, max_retries=1).get("/v1/apps")

    assert "unreachable" in str(raised.value)
    assert "10.0.0.1" not in str(raised.value)


# --- errors ----------------------------------------------------------------------------


async def test_not_found_becomes_the_domain_not_found() -> None:
    with pytest.raises(NotFoundError):
        await make_client(seen_into([], problem(404, "NOT_FOUND"))).get("/v1/apps/none")


@pytest.mark.parametrize(
    ("status", "fragment"),
    [(401, "rejected the credentials"), (403, "role does not allow"), (400, "error 400")],
)
async def test_client_errors_get_actionable_messages(status: int, fragment: str) -> None:
    with pytest.raises(UpstreamError, match=fragment):
        await make_client(seen_into([], problem(status, "PARAMETER_ERROR.INVALID", "Bad"))).get(
            "/v1/apps"
        )


@pytest.mark.parametrize("body", [b"<html>nope</html>", b"[1, 2]", b""])
async def test_unreadable_success_responses_are_upstream_errors(body: bytes) -> None:
    with pytest.raises(UpstreamError):
        await make_client(seen_into([], httpx.Response(200, content=body))).get("/v1/apps")


async def test_an_error_without_a_body_is_still_handled() -> None:
    with pytest.raises(UpstreamError, match="error 502"):
        await make_client(seen_into([], httpx.Response(502)), max_retries=0).get("/v1/apps")


# --- the quota and what is logged ------------------------------------------------------


async def test_a_nearly_spent_hourly_quota_is_logged_as_a_warning() -> None:
    answer = httpx.Response(
        200, json=APPS, headers={"X-Rate-Limit": "user-hour-lim:3500;user-hour-rem:5;"}
    )

    with structlog.testing.capture_logs() as logs:
        await make_client(seen_into([], answer)).get("/v1/apps")

    assert any(e["event"] == "asc.quota_low" and e["remaining"] == 5 for e in logs)


async def test_a_healthy_quota_is_not_noise() -> None:
    answer = httpx.Response(
        200, json=APPS, headers={"X-Rate-Limit": "user-hour-lim:3500;user-hour-rem:3000;"}
    )

    with structlog.testing.capture_logs() as logs:
        await make_client(seen_into([], answer)).get("/v1/apps")

    assert not any(e["event"] == "asc.quota_low" for e in logs)


async def test_the_token_never_appears_in_the_logs() -> None:
    requests: list[httpx.Request] = []

    with structlog.testing.capture_logs() as logs:
        await make_client(seen_into(requests)).get("/v1/apps")

    token = requests[0].headers["Authorization"].removeprefix("Bearer ")
    assert token not in json.dumps(logs, default=str)
    assert {"asc.request"} <= {e["event"] for e in logs}


async def test_the_client_can_verify_its_own_signature_with_the_public_key() -> None:
    from asc_support import generate_key_pem, token_provider

    pem = generate_key_pem()
    requests: list[httpx.Request] = []
    http = httpx.AsyncClient(transport=httpx.MockTransport(seen_into(requests)))
    client = AscClient(http, token_provider(pem))

    await client.get("/v1/apps")

    token = requests[0].headers["Authorization"].removeprefix("Bearer ")
    jwt.decode(
        token,
        public_pem(pem),
        algorithms=["ES256"],
        audience="appstoreconnect-v1",
        options={"verify_exp": False, "verify_iat": False},
    )
