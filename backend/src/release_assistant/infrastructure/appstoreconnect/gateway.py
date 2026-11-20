"""Reads from the real App Store Connect behind the assistant's ports.

Read-only for now. Writing (adding testers, replying to reviews) is deliberately not
implemented: those methods refuse with NotSupportedError, and the application is built so that
proposing such a change is refused earlier still. Turning writes on is a separate, reviewed
step, because a mistake there changes a live account.
"""

from datetime import datetime
from typing import Any

from release_assistant.domain.entities import (
    App,
    AppStoreVersion,
    BetaGroup,
    BetaTester,
    Build,
    CustomerReview,
    ReviewResponse,
)
from release_assistant.domain.errors import NotFoundError, NotSupportedError
from release_assistant.infrastructure.appstoreconnect import mapping
from release_assistant.infrastructure.appstoreconnect.client import AscClient

_PAGE = 200  # the largest page Apple allows
_WRITES_OFF = "changing App Store Connect is not enabled yet; this assistant can only read"


class AppStoreConnectGateway:
    """AppCatalog, BuildCatalog, TesterDirectory and ReviewInbox over the real service.

    Lists are cut at the client's page limit (newest first where Apple sorts that way), so a
    very long list is a recent slice rather than everything.

    Args:
        client: The authenticated HTTP client.
    """

    def __init__(self, client: AscClient) -> None:
        self._client = client

    # --- apps, versions ----------------------------------------------------------------

    async def list_apps(self) -> list[App]:
        data, _ = await self._client.get_all("/v1/apps", {"limit": _PAGE})
        return [mapping.map_app(r) for r in data]

    async def list_versions(self, app_id: str) -> list[AppStoreVersion]:
        data, _ = await self._client.get_all(
            f"/v1/apps/{app_id}/appStoreVersions", {"limit": _PAGE}
        )
        return [mapping.map_version(r, app_id) for r in data]

    # --- builds -----------------------------------------------------------------------

    async def list_builds(self, app_id: str) -> list[Build]:
        data, included = await self._client.get_all(
            "/v1/builds",
            {
                "filter[app]": app_id,
                "sort": "-uploadedDate",
                "limit": _PAGE,
                "include": "preReleaseVersion",
                "fields[builds]": "version,uploadedDate,processingState,preReleaseVersion",
                "fields[preReleaseVersions]": "version",
            },
        )
        if not data:
            # Apple answers an unknown app with an empty list; ask once so we can say not found.
            await self._client.get(f"/v1/apps/{app_id}", {"fields[apps]": "name"})
        return [mapping.map_build(r, included, app_id) for r in data]

    # --- TestFlight -------------------------------------------------------------------

    async def list_groups(self, app_id: str) -> list[BetaGroup]:
        data, _ = await self._client.get_all(f"/v1/apps/{app_id}/betaGroups", {"limit": _PAGE})
        return [mapping.map_group(r, app_id) for r in data]

    async def get_group(self, group_id: str) -> BetaGroup:
        document = await self._client.get(f"/v1/betaGroups/{group_id}", {"include": "app"})
        resource = document.get("data")
        if not isinstance(resource, dict):
            raise NotFoundError(f"Beta group {group_id!r} not found")
        return mapping.map_group(resource, mapping.group_app_id(resource))

    async def list_testers(self, group_id: str) -> list[BetaTester]:
        data, _ = await self._client.get_all(
            f"/v1/betaGroups/{group_id}/betaTesters", {"limit": _PAGE}
        )
        return [mapping.map_tester(r) for r in data]

    async def add_tester(
        self,
        group_id: str,
        email: str,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> BetaTester:
        raise NotSupportedError(_WRITES_OFF)

    async def remove_tester(self, group_id: str, tester_id: str) -> None:
        raise NotSupportedError(_WRITES_OFF)

    # --- reviews ----------------------------------------------------------------------

    async def get_review(self, review_id: str) -> CustomerReview:
        document = await self._client.get(
            f"/v1/customerReviews/{review_id}", {"include": "response"}
        )
        resource = document.get("data")
        if not isinstance(resource, dict):
            raise NotFoundError(f"Review {review_id!r} not found")
        return mapping.map_review(resource, _included(document), None)

    async def list_reviews(
        self,
        app_id: str,
        *,
        max_rating: int | None = None,
        since: datetime | None = None,
    ) -> list[CustomerReview]:
        params: dict[str, Any] = {"sort": "-createdDate", "limit": _PAGE, "include": "response"}
        if max_rating is not None:
            # Apple takes a comma-separated list of ratings to keep.
            params["filter[rating]"] = ",".join(str(r) for r in range(1, max_rating + 1))
        data, included = await self._client.get_all(f"/v1/apps/{app_id}/customerReviews", params)
        reviews = [mapping.map_review(r, included, app_id) for r in data]
        return [r for r in reviews if since is None or r.created_at >= since]

    async def respond_to_review(self, review_id: str, body: str) -> ReviewResponse:
        raise NotSupportedError(_WRITES_OFF)


def _included(document: dict[str, Any]) -> list[dict[str, Any]]:
    included = document.get("included", [])
    return included if isinstance(included, list) else []
