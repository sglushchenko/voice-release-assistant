"""Helpers for App Store Connect adapter tests: a throwaway signing key, a fake service and
JSON:API documents shaped after Apple's published OpenAPI specification (4.6.1)."""

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from release_assistant.infrastructure.appstoreconnect.auth import TokenProvider
from release_assistant.infrastructure.appstoreconnect.client import BASE_URL, AscClient
from release_assistant.infrastructure.appstoreconnect.gateway import AppStoreConnectGateway

Handler = Callable[[httpx.Request], httpx.Response]


def generate_key_pem(curve: ec.EllipticCurve | None = None) -> str:
    key = ec.generate_private_key(curve or ec.SECP256R1())
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def public_pem(private_pem: str) -> str:
    key = serialization.load_pem_private_key(private_pem.encode(), password=None)
    return (
        key.public_key()
        .public_bytes(  # type: ignore[union-attr]
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        .decode()
    )


def write_key_file(directory: Path, pem: str, *, mode: int = 0o600) -> Path:
    path = directory / "AuthKey_TESTKEY123.p8"
    path.write_text(pem)
    os.chmod(path, mode)
    return path


def token_provider(pem: str | None = None) -> TokenProvider:
    return TokenProvider(
        key_id="TESTKEY123", issuer_id="issuer-uuid", private_key_pem=pem or generate_key_pem()
    )


def make_client(handler: Handler, **kwargs: Any) -> AscClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def no_sleep(_: float) -> None:
        return None

    return AscClient(http, token_provider(), sleep=kwargs.pop("sleep", no_sleep), **kwargs)


def make_gateway(handler: Handler) -> AppStoreConnectGateway:
    return AppStoreConnectGateway(make_client(handler))


def resource(
    kind: str,
    resource_id: str,
    attributes: dict[str, Any],
    relationships: dict[str, Any] | None = None,
) -> dict[str, Any]:
    document: dict[str, Any] = {"type": kind, "id": resource_id, "attributes": attributes}
    if relationships is not None:
        document["relationships"] = relationships
    return document


def to_one(kind: str, resource_id: str) -> dict[str, Any]:
    return {"data": {"type": kind, "id": resource_id}}


def page(
    data: list[dict[str, Any]],
    *,
    included: list[dict[str, Any]] | None = None,
    next_path: str | None = None,
) -> dict[str, Any]:
    document: dict[str, Any] = {"data": data, "links": {"self": f"{BASE_URL}/x"}}
    if included:
        document["included"] = included
    if next_path:
        document["links"]["next"] = f"{BASE_URL}{next_path}"
    return document


APPS = page(
    [
        resource("apps", "1001", {"name": "Pocket Notes", "bundleId": "com.example.notes"}),
        resource("apps", "1002", {"name": "Habit Garden", "bundleId": "com.example.habits"}),
    ]
)

BUILDS = page(
    [
        resource(
            "builds",
            "b2",
            {
                "version": "42",
                "uploadedDate": "2026-11-19T15:30:00-08:00",
                "processingState": "PROCESSING",
            },
            {"preReleaseVersion": to_one("preReleaseVersions", "pv1")},
        ),
        resource(
            "builds",
            "b1",
            {"version": "41", "uploadedDate": "2026-11-18T09:00:00Z", "processingState": "VALID"},
            {"preReleaseVersion": to_one("preReleaseVersions", "pv1")},
        ),
    ],
    included=[resource("preReleaseVersions", "pv1", {"version": "1.4.0", "platform": "IOS"})],
)

VERSIONS = page(
    [
        resource(
            "appStoreVersions",
            "v1",
            {
                "versionString": "1.4.0",
                "appVersionState": "WAITING_FOR_REVIEW",
                "appStoreState": "WAITING_FOR_REVIEW",
            },
        ),
        resource(
            "appStoreVersions",
            "v2",
            {"versionString": "1.3.0", "appStoreState": "METADATA_REJECTED"},
        ),
        resource(
            "appStoreVersions", "v3", {"versionString": "1.2.0", "appVersionState": "ACCEPTED"}
        ),
    ]
)

GROUP = {
    "data": resource(
        "betaGroups",
        "g1",
        {"name": "Internal", "isInternalGroup": True},
        {"app": to_one("apps", "1001")},
    ),
    "included": [resource("apps", "1001", {"name": "Pocket Notes"})],
}

GROUPS = page(
    [
        resource("betaGroups", "g1", {"name": "Internal"}),
        resource("betaGroups", "g2", {"name": "Public Beta"}),
    ]
)

TESTERS = page(
    [
        resource(
            "betaTesters", "t1", {"email": "Anna@Example.com", "firstName": "Anna", "lastName": ""}
        ),
        resource(
            "betaTesters", "t2", {"email": "boris@example.com", "firstName": None, "lastName": None}
        ),
    ]
)

REVIEWS = page(
    [
        resource(
            "customerReviews",
            "r2",
            {
                "rating": 1,
                "title": "Crashes on launch",
                "body": "It crashes.",
                "reviewerNickname": "x",
                "createdDate": "2026-11-18T18:45:00Z",
                "territory": "DEU",
            },
            {"response": to_one("customerReviewResponses", "resp1")},
        ),
        resource(
            "customerReviews",
            "r1",
            {
                "rating": 5,
                "title": "Love it",
                "body": "Fast.",
                "reviewerNickname": "y",
                "createdDate": "2026-11-10T07:20:00Z",
                "territory": "USA",
            },
            {"response": {"data": None}},
        ),
    ],
    included=[
        resource(
            "customerReviewResponses",
            "resp1",
            {
                "responseBody": "Sorry!",
                "lastModifiedDate": "2026-11-19T10:00:00Z",
                "state": "PUBLISHED",
            },
        )
    ],
)


def not_found() -> httpx.Response:
    return httpx.Response(
        404, json={"errors": [{"status": "404", "code": "NOT_FOUND", "title": "Not found"}]}
    )
