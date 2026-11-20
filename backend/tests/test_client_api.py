import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from release_assistant.api.app import create_app
from release_assistant.api.openapi_export import render
from release_assistant.bootstrap import build_mock_application
from release_assistant.config import Settings

CONTRACT = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"


def make_client(base_url: str = "http://testserver", **overrides: object) -> TestClient:
    settings = Settings(_env_file=None, **overrides)  # type: ignore[arg-type]
    return TestClient(create_app(settings, build_mock_application()), base_url=base_url)


# --- interactive docs ------------------------------------------------------------------


def test_docs_and_schema_are_hidden_by_default() -> None:
    client = make_client()

    assert {client.get(path).status_code for path in ("/docs", "/openapi.json")} == {404}


def test_docs_can_be_enabled_explicitly() -> None:
    client = make_client(docs_enabled=True)

    assert client.get("/openapi.json").status_code == 200
    docs = client.get("/docs")
    assert docs.status_code == 200
    assert "content-security-policy" not in docs.headers


# --- security headers ------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/healthz", "/v1/apps", "/nowhere"])
def test_every_response_carries_the_defensive_headers(path: str) -> None:
    response = make_client().get(path)

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"
    assert (
        response.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    )


def test_refusals_from_other_middleware_carry_them_too() -> None:
    client = make_client(rate_limits={"ip": "1/minute"})
    client.get("/v1/apps")

    refused = client.get("/v1/apps")

    assert refused.status_code == 429
    assert refused.headers["x-content-type-options"] == "nosniff"


def test_hsts_is_sent_only_over_https() -> None:
    assert "strict-transport-security" not in make_client().get("/healthz").headers
    secure = make_client(base_url="https://testserver").get("/healthz")

    assert secure.headers["strict-transport-security"].startswith("max-age=")


# --- CORS ------------------------------------------------------------------------------

ALLOWED = "https://app.example.com"


def preflight(client: TestClient, origin: str) -> object:
    return client.options(
        "/v1/apps",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )


def test_cors_is_off_by_default() -> None:
    response = preflight(make_client(), ALLOWED)

    assert "access-control-allow-origin" not in response.headers  # type: ignore[attr-defined]


def test_an_allowed_origin_passes_the_preflight_without_credentials() -> None:
    client = make_client(cors_origins=[ALLOWED])

    response = preflight(client, ALLOWED)

    assert response.status_code == 200  # type: ignore[attr-defined]
    assert response.headers["access-control-allow-origin"] == ALLOWED  # type: ignore[attr-defined]
    assert "authorization" in response.headers["access-control-allow-headers"].lower()  # type: ignore[attr-defined]
    assert "access-control-allow-credentials" not in response.headers  # type: ignore[attr-defined]


def test_another_origin_gets_no_cors_approval() -> None:
    response = preflight(make_client(cors_origins=[ALLOWED]), "https://evil.example.net")

    assert "access-control-allow-origin" not in response.headers  # type: ignore[attr-defined]


def test_the_preflight_needs_no_credentials_and_is_not_rate_limited() -> None:
    client = make_client(cors_origins=[ALLOWED], rate_limits={"ip": "1/minute"})

    statuses = [preflight(client, ALLOWED).status_code for _ in range(5)]  # type: ignore[attr-defined]

    assert statuses == [200] * 5


def test_real_responses_expose_the_headers_a_client_needs() -> None:
    client = make_client(cors_origins=[ALLOWED])

    response = client.get("/v1/apps", headers={"Origin": ALLOWED})

    exposed = response.headers["access-control-expose-headers"].lower()
    assert "x-request-id" in exposed
    assert "retry-after" in exposed


@pytest.mark.parametrize(
    "origin",
    ["*", "https://app.example.com/", "https://app.example.com/path", "app.example.com", ""],
)
def test_vague_origins_stop_startup(origin: str) -> None:
    with pytest.raises(ValidationError, match="exact origin"):
        Settings(_env_file=None, cors_origins=[origin])


def test_exact_origins_with_ports_are_accepted() -> None:
    settings = Settings(_env_file=None, cors_origins=["http://localhost:5173", ALLOWED])

    assert len(settings.cors_origins) == 2


# --- the contract ----------------------------------------------------------------------


def test_the_committed_contract_matches_the_code() -> None:
    assert CONTRACT.read_text(encoding="utf-8") == render(), (
        "docs/openapi.json is out of date: run `make openapi` in backend/ and commit the result"
    )


def test_operation_ids_are_unique_and_readable() -> None:
    spec = json.loads(render())
    ids = [op["operationId"] for item in spec["paths"].values() for op in item.values()]

    assert len(ids) == len(set(ids))
    assert "catalog_list_apps" in ids
    assert not [i for i in ids if i.endswith(("_get", "_post"))]


def test_every_api_operation_is_authenticated_and_documents_its_errors() -> None:
    spec = json.loads(render())
    public = {"/healthz", "/v1/auth/login"}

    for path, item in spec["paths"].items():
        for method, operation in item.items():
            where = f"{method.upper()} {path}"
            if path in public:
                continue
            assert "401" in operation["responses"], where
            body = operation["responses"]["401"]["content"]["application/json"]["schema"]
            assert body == {"$ref": "#/components/schemas/Problem"}, where


def test_the_contract_describes_bearer_authentication() -> None:
    spec = json.loads(render())

    assert spec["components"]["securitySchemes"]["HTTPBearer"] == {
        "type": "http",
        "scheme": "bearer",
    }


def test_the_approval_mode_is_a_string_enum_in_the_contract() -> None:
    spec = json.loads(render())

    mode = spec["components"]["schemas"]["ApprovalMode"]
    assert mode["type"] == "string"
    assert mode["enum"] == ["implicit_if_clear", "voice_or_button", "button_only"]
