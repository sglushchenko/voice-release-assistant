"""App Store funnel analytics: impressions, product page views and downloads over a period.

Read-only, so nothing here goes through confirmation. The source publishes with a delay and
may keep only a window of history, so every report says how much of the requested period
(and of the comparison period) the data actually covers.
"""

from datetime import date, timedelta

from release_assistant.application.stats import change_pct, ratio_pct
from release_assistant.domain.entities import (
    App,
    DateCoverage,
    EngagementRow,
    Entity,
)
from release_assistant.domain.errors import DataUnavailableError, NotFoundError
from release_assistant.domain.ports import AppCatalog, EngagementReports


class EngagementTotals(Entity):
    impressions: int = 0
    product_page_views: int = 0
    downloads: int = 0


class DailyEngagement(Entity):
    day: date
    totals: EngagementTotals


class AppEngagement(Entity):
    app: App
    totals: EngagementTotals


class EngagementChanges(Entity):
    """Percent change versus the previous period; None where it has no meaning."""

    impressions: float | None
    product_page_views: float | None
    downloads: float | None


class EngagementReport(Entity):
    """
    Args:
        app: The app, or None for all apps.
        totals: Funnel counts for `start`..`end`.
        previous: Counts for the equal-length period just before.
        tap_through_pct: Page views as a percentage of impressions.
        conversion_pct: Downloads as a percentage of page views.
        daily: One entry per day in the period, zero-filled.
        by_app: Breakdown for all-apps reports, best first by impressions.
        coverage: Range the source has published.
        is_complete: The source covers the whole requested period.
        previous_is_complete: The source covers the whole comparison period; when False the
            change figures understate or are missing.
    """

    app: App | None
    start: date
    end: date
    previous_start: date
    previous_end: date
    totals: EngagementTotals
    previous: EngagementTotals
    changes: EngagementChanges
    tap_through_pct: float | None
    conversion_pct: float | None
    daily: list[DailyEngagement]
    by_app: list[AppEngagement]
    coverage: DateCoverage
    is_complete: bool
    previous_is_complete: bool


def _sum(rows: list[EngagementRow]) -> EngagementTotals:
    return EngagementTotals(
        impressions=sum(r.impressions for r in rows),
        product_page_views=sum(r.product_page_views for r in rows),
        downloads=sum(r.downloads for r in rows),
    )


class EngagementService:
    """
    Args:
        reports: Source of funnel data; mock or real.
        catalog: Used to validate app ids and to name apps.
    """

    def __init__(self, reports: EngagementReports, catalog: AppCatalog) -> None:
        self._reports = reports
        self._catalog = catalog

    async def report(
        self, start: date, end: date, *, app_id: str | None = None
    ) -> EngagementReport:
        """Funnel report for an inclusive period, compared with the period just before it.

        Args:
            start: First day.
            end: Last day; not before `start`.
            app_id: Restrict to one app; None covers all apps and adds a per-app breakdown.
        """
        if end < start:
            raise ValueError("end must not be before start")
        apps = {a.id: a for a in await self._catalog.list_apps()}
        if app_id is not None and app_id not in apps:
            raise NotFoundError(f"App {app_id!r} not found")

        length = (end - start).days + 1
        previous_end = start - timedelta(days=1)
        previous_start = previous_end - timedelta(days=length - 1)
        coverage = await self._reports.engagement_coverage()
        rows = [
            r
            for r in await self._reports.list_engagement(previous_start, end)
            if app_id is None or r.app_id == app_id
        ]
        current_rows = [r for r in rows if r.day >= start]
        previous_rows = [r for r in rows if r.day < start]
        totals, previous = _sum(current_rows), _sum(previous_rows)

        return EngagementReport(
            app=apps[app_id] if app_id else None,
            start=start,
            end=end,
            previous_start=previous_start,
            previous_end=previous_end,
            totals=totals,
            previous=previous,
            changes=EngagementChanges(
                impressions=change_pct(totals.impressions, previous.impressions),
                product_page_views=change_pct(
                    totals.product_page_views, previous.product_page_views
                ),
                downloads=change_pct(totals.downloads, previous.downloads),
            ),
            tap_through_pct=ratio_pct(totals.product_page_views, totals.impressions),
            conversion_pct=ratio_pct(totals.downloads, totals.product_page_views),
            daily=self._daily(current_rows, start, length),
            by_app=[] if app_id else self._by_app(current_rows, apps),
            coverage=coverage,
            is_complete=coverage.covers(start, end),
            previous_is_complete=coverage.covers(previous_start, previous_end),
        )

    async def recent(self, days: int, *, app_id: str | None = None) -> EngagementReport:
        """The last `days` days of published data.

        Ends at the latest published day, not at today, because today is never available yet.
        Raises DataUnavailableError before the first report has been generated.
        """
        if days < 1:
            raise ValueError("days must be positive")
        coverage = await self._reports.engagement_coverage()
        if coverage.last_day is None:
            raise DataUnavailableError("No engagement data has been published yet")
        end = coverage.last_day
        return await self.report(end - timedelta(days=days - 1), end, app_id=app_id)

    @staticmethod
    def _daily(rows: list[EngagementRow], start: date, length: int) -> list[DailyEngagement]:
        by_day: dict[date, list[EngagementRow]] = {}
        for row in rows:
            by_day.setdefault(row.day, []).append(row)
        days = (start + timedelta(days=offset) for offset in range(length))
        return [DailyEngagement(day=d, totals=_sum(by_day.get(d, []))) for d in days]

    @staticmethod
    def _by_app(rows: list[EngagementRow], apps: dict[str, App]) -> list[AppEngagement]:
        totals = [
            AppEngagement(app=app, totals=_sum(app_rows))
            for app in apps.values()
            if (app_rows := [r for r in rows if r.app_id == app.id])
        ]
        return sorted(totals, key=lambda t: (-t.totals.impressions, t.app.name))
