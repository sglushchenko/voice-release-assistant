"""In-memory App Store Connect stand-in.

Use for unit tests, evals, benchmarks and demos: deterministic, free and safe to run
against a public repository. Implements every port, so it can be swapped for the real
adapter without touching the layers above.
"""

from collections.abc import Iterable
from datetime import datetime

from release_assistant.domain.entities import (
    App,
    AppStoreVersion,
    BetaGroup,
    BetaTester,
    Build,
    CustomerReview,
    ReviewResponse,
    ReviewResponseState,
)
from release_assistant.domain.errors import AlreadyExistsError, NotFoundError


class MockAppStoreGateway:
    """Implements AppCatalog, TesterDirectory and ReviewInbox over in-memory state.

    Args:
        memberships: group id -> ids of testers in that group.
    """

    def __init__(
        self,
        *,
        apps: Iterable[App] = (),
        builds: Iterable[Build] = (),
        versions: Iterable[AppStoreVersion] = (),
        groups: Iterable[BetaGroup] = (),
        testers: Iterable[BetaTester] = (),
        memberships: dict[str, list[str]] | None = None,
        reviews: Iterable[CustomerReview] = (),
    ) -> None:
        self._apps = {a.id: a for a in apps}
        self._builds = list(builds)
        self._versions = list(versions)
        self._groups = {g.id: g for g in groups}
        self._testers = {t.id: t for t in testers}
        self._memberships = {gid: list(ids) for gid, ids in (memberships or {}).items()}
        self._reviews = {r.id: r for r in reviews}
        self._sequence = 0

    def _next_id(self, prefix: str) -> str:
        self._sequence += 1
        return f"{prefix}-{self._sequence}"

    def _require_app(self, app_id: str) -> None:
        if app_id not in self._apps:
            raise NotFoundError(f"App {app_id!r} not found")

    def _require_group(self, group_id: str) -> None:
        if group_id not in self._groups:
            raise NotFoundError(f"Beta group {group_id!r} not found")

    async def list_apps(self) -> list[App]:
        return list(self._apps.values())

    async def list_builds(self, app_id: str) -> list[Build]:
        self._require_app(app_id)
        builds = [b for b in self._builds if b.app_id == app_id]
        return sorted(builds, key=lambda b: b.uploaded_at, reverse=True)

    async def list_versions(self, app_id: str) -> list[AppStoreVersion]:
        self._require_app(app_id)
        return [v for v in self._versions if v.app_id == app_id]

    async def list_groups(self, app_id: str) -> list[BetaGroup]:
        self._require_app(app_id)
        return [g for g in self._groups.values() if g.app_id == app_id]

    async def list_testers(self, group_id: str) -> list[BetaTester]:
        self._require_group(group_id)
        return [self._testers[tid] for tid in self._memberships.get(group_id, [])]

    async def add_tester(
        self,
        group_id: str,
        email: str,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> BetaTester:
        self._require_group(group_id)
        normalized = email.strip().lower()
        tester = next((t for t in self._testers.values() if t.email == normalized), None)
        if tester is None:
            tester = BetaTester(
                id=self._next_id("tester"),
                email=normalized,
                first_name=first_name,
                last_name=last_name,
            )
            self._testers[tester.id] = tester
        members = self._memberships.setdefault(group_id, [])
        if tester.id in members:
            raise AlreadyExistsError(f"{normalized} is already in group {group_id!r}")
        members.append(tester.id)
        return tester

    async def remove_tester(self, group_id: str, tester_id: str) -> None:
        self._require_group(group_id)
        members = self._memberships.get(group_id, [])
        if tester_id not in members:
            raise NotFoundError(f"Tester {tester_id!r} is not in group {group_id!r}")
        members.remove(tester_id)

    async def list_reviews(
        self,
        app_id: str,
        *,
        max_rating: int | None = None,
        since: datetime | None = None,
    ) -> list[CustomerReview]:
        self._require_app(app_id)
        reviews = [
            r
            for r in self._reviews.values()
            if r.app_id == app_id
            and (max_rating is None or r.rating <= max_rating)
            and (since is None or r.created_at >= since)
        ]
        return sorted(reviews, key=lambda r: r.created_at, reverse=True)

    async def respond_to_review(self, review_id: str, body: str) -> ReviewResponse:
        review = self._reviews.get(review_id)
        if review is None:
            raise NotFoundError(f"Review {review_id!r} not found")
        if review.response is not None:
            raise AlreadyExistsError(f"Review {review_id!r} already has a response")
        response = ReviewResponse(
            id=self._next_id("response"),
            review_id=review_id,
            body=body,
            state=ReviewResponseState.PENDING_PUBLISH,
        )
        self._reviews[review_id] = review.model_copy(update={"response": response})
        return response
