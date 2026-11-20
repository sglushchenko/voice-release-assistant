import hashlib
from typing import Any

import pytest
from fastapi.testclient import TestClient

from release_assistant.api.app import create_app
from release_assistant.bootstrap import build_mock_application
from release_assistant.config import ApiClient, Scope, Settings


def sha256(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


AGENT = headers("agent-key")  # read + propose, no way to confirm
APP = headers("app-key")  # the user's own app: confirms by voice or button
VOICE_ONLY = headers("voice-key")
MALLORY = headers("mallory-key")  # a different account with full rights

ADD_TESTER = {
    "action": "add_tester",
    "arguments": {"group_id": "group-1", "email": "new@example.com"},
}


def api_client(subject: str, owner: str, key: str, *scopes: Scope) -> ApiClient:
    return ApiClient(subject=subject, owner=owner, key_sha256=sha256(key), scopes=frozenset(scopes))


@pytest.fixture
def client() -> TestClient:
    settings = Settings(
        api_clients=[
            api_client("agent", "alice", "agent-key", "read", "propose"),
            api_client("app", "alice", "app-key", "read", "confirm:button", "confirm:voice"),
            api_client("voice", "alice", "voice-key", "read", "confirm:voice"),
            api_client(
                "mallory",
                "mallory",
                "mallory-key",
                "read",
                "propose",
                "confirm:button",
                "confirm:voice",
            ),
        ]
    )
    return TestClient(create_app(settings, build_mock_application()))


def new_session(client: TestClient, who: dict[str, str] = AGENT) -> str:
    response = client.post("/v1/sessions", headers=who)
    assert response.status_code == 201
    return response.json()["session_id"]


def propose(client: TestClient, session_id: str, body: dict[str, Any] = ADD_TESTER) -> Any:
    return client.post(f"/v1/sessions/{session_id}/actions", json=body, headers=AGENT)


def confirm(client: TestClient, action_id: str, channel: str, who: dict[str, str] = APP) -> Any:
    return client.post(f"/v1/actions/{action_id}/confirm", json={"channel": channel}, headers=who)


def test_agent_proposes_and_the_users_app_confirms_by_voice(client: TestClient) -> None:
    session_id = new_session(client)

    proposed = propose(client, session_id)
    action = proposed.json()["action"]
    confirmed = confirm(client, action["id"], "voice")

    assert proposed.status_code == 202
    assert proposed.json()["status"] == "pending"
    assert action["summary"] == "Add tester new@example.com to TestFlight group 'Internal'"
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "executed"
    testers = client.get("/v1/beta-groups/group-1/testers", headers=AGENT).json()
    assert "new@example.com" in [t["email"] for t in testers]


def test_the_agent_credential_cannot_confirm_anything(client: TestClient) -> None:
    action_id = propose(client, new_session(client)).json()["action"]["id"]

    for channel in ("voice", "button"):
        response = confirm(client, action_id, channel, who=AGENT)
        assert response.status_code == 403
        assert response.json()["code"] == "forbidden"

    testers = client.get("/v1/beta-groups/group-1/testers", headers=AGENT).json()
    assert [t["email"] for t in testers] == ["anna@example.com"]


def test_a_client_cannot_claim_a_channel_its_credential_lacks(client: TestClient) -> None:
    action_id = propose(client, new_session(client)).json()["action"]["id"]

    response = confirm(client, action_id, "button", who=VOICE_ONLY)

    assert response.status_code == 403
    assert response.json()["detail"] == "Missing scope: confirm:button"


def test_reading_reviews_in_a_session_makes_confirmation_button_only(client: TestClient) -> None:
    session_id = new_session(client)
    reviews = client.get(f"/v1/sessions/{session_id}/apps/app-1/reviews", headers=AGENT).json()
    action = propose(client, session_id).json()["action"]

    by_voice = confirm(client, action["id"], "voice")
    by_button = confirm(client, action["id"], "button")

    assert (reviews["source"], reviews["trust"]) == ("customer_reviews", "untrusted")
    assert len(reviews["reviews"]) == 3
    assert action["mode"] == "button_only"
    assert by_voice.status_code == 403
    assert by_voice.json()["code"] == "channel_not_allowed"
    assert by_button.status_code == 200


def test_another_session_is_not_tainted(client: TestClient) -> None:
    tainted = new_session(client)
    clean = new_session(client)
    client.get(f"/v1/sessions/{tainted}/apps/app-1/reviews", headers=AGENT)

    action = propose(client, clean).json()["action"]

    assert action["mode"] == "voice_or_button"


def test_a_review_reply_is_a_public_write_needing_confirmation(client: TestClient) -> None:
    session_id = new_session(client)
    body = {
        "action": "respond_to_review",
        "arguments": {"review_id": "review-2", "body": "Fixed in 1.4.1."},
    }
    action = propose(client, session_id, body).json()["action"]

    assert action["tier"] == "public_write"
    assert "Fixed in 1.4.1." in action["summary"]
    assert confirm(client, action["id"], "button").status_code == 200
    unanswered = client.get(
        f"/v1/sessions/{session_id}/apps/app-1/reviews/unanswered", headers=AGENT
    ).json()
    assert "review-2" not in [r["id"] for r in unanswered["reviews"]]


def test_another_account_sees_not_found_for_sessions_and_actions(client: TestClient) -> None:
    session_id = new_session(client)
    action_id = propose(client, session_id).json()["action"]["id"]

    stolen_session = client.get(f"/v1/sessions/{session_id}/apps/app-1/reviews", headers=MALLORY)
    stolen_proposal = client.post(
        f"/v1/sessions/{session_id}/actions", json=ADD_TESTER, headers=MALLORY
    )
    peek = client.get(f"/v1/actions/{action_id}", headers=MALLORY)
    steal = confirm(client, action_id, "button", who=MALLORY)

    assert {r.status_code for r in (stolen_session, stolen_proposal, peek, steal)} == {404}
    assert client.get("/v1/actions", headers=MALLORY).json() == []
    assert confirm(client, action_id, "button").status_code == 200


def test_rejecting_cancels_and_blocks_a_later_confirmation(client: TestClient) -> None:
    action_id = propose(client, new_session(client)).json()["action"]["id"]

    rejected = client.post(f"/v1/actions/{action_id}/reject", headers=AGENT)
    late = confirm(client, action_id, "button")

    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert late.status_code == 409
    assert late.json()["code"] == "action_not_pending"


def test_listing_actions_by_status(client: TestClient) -> None:
    session_id = new_session(client)
    first = propose(client, session_id).json()["action"]["id"]
    second = propose(
        client,
        session_id,
        {"action": "add_tester", "arguments": {"group_id": "group-1", "email": "two@example.com"}},
    ).json()["action"]["id"]
    client.post(f"/v1/actions/{first}/reject", headers=AGENT)

    pending = client.get("/v1/actions", params={"status": "pending"}, headers=APP).json()

    assert [a["id"] for a in pending] == [second]


def test_proposing_requires_the_propose_scope(client: TestClient) -> None:
    session_id = new_session(client)

    response = client.post(f"/v1/sessions/{session_id}/actions", json=ADD_TESTER, headers=APP)

    assert response.status_code == 403


def test_bad_proposals_are_rejected_with_clear_errors(client: TestClient) -> None:
    session_id = new_session(client)
    bad_args = propose(
        client, session_id, {"action": "add_tester", "arguments": {"group_id": "group-1"}}
    )
    unknown = propose(client, session_id, {"action": "launch_missiles", "arguments": {}})
    extra = propose(client, session_id, {**ADD_TESTER, "owner": "bob"})
    gone_group = propose(
        client,
        session_id,
        {"action": "add_tester", "arguments": {"group_id": "nope", "email": "a@example.com"}},
    )

    assert (bad_args.status_code, bad_args.json()["code"]) == (422, "invalid_arguments")
    assert (unknown.status_code, unknown.json()["code"]) == (404, "not_found")
    assert extra.status_code == 422
    assert gone_group.status_code == 404
    assert client.get("/v1/actions", headers=APP).json() == []


def test_the_session_limit_returns_429(client: TestClient) -> None:
    for _ in range(20):
        new_session(client)

    assert client.post("/v1/sessions", headers=AGENT).status_code == 429


def test_session_creation_requires_credentials(client: TestClient) -> None:
    assert client.post("/v1/sessions").status_code == 401
