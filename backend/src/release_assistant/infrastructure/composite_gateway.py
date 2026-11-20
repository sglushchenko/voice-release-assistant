"""Assembling one AppStoreGateway from parts, and the stubs for what is not supported yet."""

from datetime import date, datetime

from release_assistant.domain.entities import (
    App,
    AppStoreVersion,
    BetaGroup,
    BetaTester,
    Build,
    CustomerReview,
    DateCoverage,
    EngagementRow,
    ReviewResponse,
    SalesRow,
)
from release_assistant.domain.errors import NotSupportedError
from release_assistant.domain.ports import (
    AppCatalog,
    BuildCatalog,
    EngagementReports,
    ReviewInbox,
    SalesReports,
    TesterDirectory,
)


class UnsupportedReports:
    """Sales and engagement for a backend that has no reports yet. Every call says so plainly,
    which is what a person asking the assistant about revenue is told."""

    def __init__(self, backend: str) -> None:
        self._message = f"{backend} sales and engagement reports are not supported yet"

    async def list_sales(self, start: date, end: date) -> list[SalesRow]:
        raise NotSupportedError(self._message)

    async def engagement_coverage(self) -> DateCoverage:
        raise NotSupportedError(self._message)

    async def list_engagement(self, start: date, end: date) -> list[EngagementRow]:
        raise NotSupportedError(self._message)


class CompositeGateway:
    """Delegates each port to the part that implements it, so a real backend can serve what it
    supports while the rest answers "not supported yet"."""

    def __init__(
        self,
        *,
        catalog: AppCatalog,
        builds: BuildCatalog,
        testers: TesterDirectory,
        reviews: ReviewInbox,
        sales: SalesReports,
        engagement: EngagementReports,
    ) -> None:
        self._catalog = catalog
        self._builds = builds
        self._testers = testers
        self._reviews = reviews
        self._sales = sales
        self._engagement = engagement

    async def list_apps(self) -> list[App]:
        return await self._catalog.list_apps()

    async def list_versions(self, app_id: str) -> list[AppStoreVersion]:
        return await self._catalog.list_versions(app_id)

    async def list_builds(self, app_id: str) -> list[Build]:
        return await self._builds.list_builds(app_id)

    async def list_groups(self, app_id: str) -> list[BetaGroup]:
        return await self._testers.list_groups(app_id)

    async def get_group(self, group_id: str) -> BetaGroup:
        return await self._testers.get_group(group_id)

    async def list_testers(self, group_id: str) -> list[BetaTester]:
        return await self._testers.list_testers(group_id)

    async def add_tester(
        self,
        group_id: str,
        email: str,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> BetaTester:
        return await self._testers.add_tester(group_id, email, first_name, last_name)

    async def remove_tester(self, group_id: str, tester_id: str) -> None:
        await self._testers.remove_tester(group_id, tester_id)

    async def get_review(self, review_id: str) -> CustomerReview:
        return await self._reviews.get_review(review_id)

    async def list_reviews(
        self, app_id: str, *, max_rating: int | None = None, since: datetime | None = None
    ) -> list[CustomerReview]:
        return await self._reviews.list_reviews(app_id, max_rating=max_rating, since=since)

    async def respond_to_review(self, review_id: str, body: str) -> ReviewResponse:
        return await self._reviews.respond_to_review(review_id, body)

    async def list_sales(self, start: date, end: date) -> list[SalesRow]:
        return await self._sales.list_sales(start, end)

    async def engagement_coverage(self) -> DateCoverage:
        return await self._engagement.engagement_coverage()

    async def list_engagement(self, start: date, end: date) -> list[EngagementRow]:
        return await self._engagement.list_engagement(start, end)
