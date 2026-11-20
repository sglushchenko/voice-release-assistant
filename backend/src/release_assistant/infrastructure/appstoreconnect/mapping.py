"""Turning App Store Connect's JSON:API resources into the assistant's entities.

The shapes follow Apple's published OpenAPI specification (version 4.6.1): a resource is
`{"id", "type", "attributes": {...}, "relationships": {...}}` and related resources arrive in a
top-level `included` list. Mapping is pure so it can be tested without a network.
"""

from datetime import datetime
from typing import Any

from release_assistant.domain.entities import (
    App,
    AppStoreVersion,
    AppStoreVersionState,
    BetaGroup,
    BetaTester,
    Build,
    BuildProcessingState,
    CustomerReview,
    ReviewResponse,
    ReviewResponseState,
)
from release_assistant.domain.errors import UpstreamError

Resource = dict[str, Any]

_VERSION_STATES: dict[str, AppStoreVersionState] = {
    "PREPARE_FOR_SUBMISSION": AppStoreVersionState.PREPARE_FOR_SUBMISSION,
    "WAITING_FOR_REVIEW": AppStoreVersionState.WAITING_FOR_REVIEW,
    "IN_REVIEW": AppStoreVersionState.IN_REVIEW,
    "PENDING_DEVELOPER_RELEASE": AppStoreVersionState.PENDING_DEVELOPER_RELEASE,
    "READY_FOR_SALE": AppStoreVersionState.READY_FOR_SALE,
    # `appVersionState` calls the same thing "ready for distribution".
    "READY_FOR_DISTRIBUTION": AppStoreVersionState.READY_FOR_SALE,
    # Every way review can say no is a rejection the developer must respond to.
    "REJECTED": AppStoreVersionState.REJECTED,
    "METADATA_REJECTED": AppStoreVersionState.REJECTED,
    "DEVELOPER_REJECTED": AppStoreVersionState.REJECTED,
    "INVALID_BINARY": AppStoreVersionState.REJECTED,
}


def _attributes(resource: Resource) -> dict[str, Any]:
    attributes = resource.get("attributes")
    return attributes if isinstance(attributes, dict) else {}


def _datetime(value: Any) -> datetime:
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        raise UpstreamError("App Store Connect sent a date in an unexpected format") from None


def _index(included: list[Resource]) -> dict[tuple[str, str], Resource]:
    return {
        (item["type"], item["id"]): item for item in included if "type" in item and "id" in item
    }


def _related_id(resource: Resource, name: str) -> str | None:
    data = resource.get("relationships", {}).get(name, {}).get("data")
    return str(data["id"]) if isinstance(data, dict) and "id" in data else None


def map_app(resource: Resource) -> App:
    attributes = _attributes(resource)
    return App(
        id=str(resource["id"]),
        name=str(attributes.get("name", "")),
        bundle_id=str(attributes.get("bundleId", "")),
    )


def map_build(resource: Resource, included: list[Resource], app_id: str) -> Build:
    """`attributes.version` is the build number; the marketing version (1.4.0) lives on the
    related pre-release version, which the request asks Apple to include."""
    attributes = _attributes(resource)
    pre_release_id = _related_id(resource, "preReleaseVersion")
    pre_release = _index(included).get(("preReleaseVersions", pre_release_id or ""))
    try:
        state = BuildProcessingState(str(attributes.get("processingState")))
    except ValueError:
        raise UpstreamError("App Store Connect sent an unknown build processing state") from None
    return Build(
        id=str(resource["id"]),
        app_id=app_id,
        version=str(_attributes(pre_release).get("version", "")) if pre_release else "",
        build_number=str(attributes.get("version", "")),
        processing_state=state,
        uploaded_at=_datetime(attributes.get("uploadedDate")),
    )


def map_version_state(apple_state: str | None) -> AppStoreVersionState:
    return _VERSION_STATES.get(apple_state or "", AppStoreVersionState.OTHER)


def map_version(resource: Resource, app_id: str) -> AppStoreVersion:
    attributes = _attributes(resource)
    # Both fields exist in the current API; the newer one is preferred.
    apple_state = attributes.get("appVersionState") or attributes.get("appStoreState")
    return AppStoreVersion(
        id=str(resource["id"]),
        app_id=app_id,
        version_string=str(attributes.get("versionString", "")),
        state=map_version_state(apple_state),
        apple_state=str(apple_state) if apple_state else None,
    )


def map_group(resource: Resource, app_id: str) -> BetaGroup:
    return BetaGroup(
        id=str(resource["id"]), app_id=app_id, name=str(_attributes(resource).get("name", ""))
    )


def group_app_id(resource: Resource) -> str:
    """The app a group belongs to, read from the included relationship."""
    app_id = _related_id(resource, "app")
    if app_id is None:
        raise UpstreamError("App Store Connect did not say which app the group belongs to")
    return app_id


def map_tester(resource: Resource) -> BetaTester:
    attributes = _attributes(resource)
    return BetaTester(
        id=str(resource["id"]),
        email=str(attributes.get("email", "")).lower(),
        first_name=attributes.get("firstName") or None,
        last_name=attributes.get("lastName") or None,
    )


def map_review(resource: Resource, included: list[Resource], app_id: str | None) -> CustomerReview:
    attributes = _attributes(resource)
    response_id = _related_id(resource, "response")
    response = _index(included).get(("customerReviewResponses", response_id or ""))
    return CustomerReview(
        id=str(resource["id"]),
        app_id=app_id,
        rating=int(attributes.get("rating", 1)),
        title=str(attributes.get("title", "")),
        body=str(attributes.get("body", "")),
        territory=str(attributes.get("territory", "")),
        created_at=_datetime(attributes.get("createdDate")),
        response=_map_response(response, str(resource["id"])) if response else None,
    )


def _map_response(resource: Resource, review_id: str) -> ReviewResponse:
    attributes = _attributes(resource)
    try:
        state = ReviewResponseState(str(attributes.get("state")))
    except ValueError:
        state = ReviewResponseState.PENDING_PUBLISH
    return ReviewResponse(
        id=str(resource["id"]),
        review_id=review_id,
        body=str(attributes.get("responseBody", "")),
        state=state,
    )
