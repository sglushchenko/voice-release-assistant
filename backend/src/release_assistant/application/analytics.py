"""Monthly sales analytics built from Sales and Trends data.

Read-only, so nothing here goes through confirmation. All money is an estimate and is kept
per currency: adding USD to EUR without an exchange rate would produce a wrong number that
looks right. Settled payouts (Finance reports) are a separate future source.
"""

import calendar
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal

from release_assistant.domain.entities import App, Entity, SalesKind, SalesRow
from release_assistant.domain.errors import NotFoundError
from release_assistant.domain.ports import AppCatalog, SalesReports


class MonthSummary(Entity):
    """Totals for one calendar month.

    Args:
        downloads: First-time downloads. Updates are excluded.
        proceeds: Estimated developer proceeds by currency code.
        last_data_day: Latest day with any data; None when there is none.
        is_partial: The month has not ended yet, so the figures are still growing.
    """

    year: int
    month: int
    downloads: int
    updates: int
    in_app_purchases: int
    proceeds: dict[str, Decimal]
    last_data_day: date | None
    is_partial: bool


class TerritoryTotal(Entity):
    territory: str
    downloads: int


class AppTotal(Entity):
    app: App
    downloads: int
    proceeds: dict[str, Decimal]


class MonthlyReport(Entity):
    """`downloads_change_pct` is None when the previous month had no downloads, where a
    percentage would be meaningless. `app` is None for a report across all apps."""

    app: App | None
    current: MonthSummary
    previous: MonthSummary
    downloads_change_pct: float | None
    top_territories: list[TerritoryTotal]
    by_app: list[AppTotal]


def _utc_today() -> date:
    return datetime.now(UTC).date()


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _previous_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _proceeds_by_currency(rows: list[SalesRow]) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for row in rows:
        totals[row.currency] += row.proceeds
    return dict(sorted(totals.items()))


def _units(rows: list[SalesRow], kind: SalesKind) -> int:
    return sum(r.units for r in rows if r.kind is kind)


class AnalyticsService:
    """
    Args:
        sales: Source of Sales and Trends rows; mock or real.
        catalog: Used to validate app ids and to name apps in the report.
        today: Injected so tests control which months count as complete.
    """

    def __init__(
        self,
        sales: SalesReports,
        catalog: AppCatalog,
        *,
        today: Callable[[], date] = _utc_today,
    ) -> None:
        self._sales = sales
        self._catalog = catalog
        self._today = today

    async def monthly_report(
        self, year: int, month: int, *, app_id: str | None = None, top_territories: int = 5
    ) -> MonthlyReport:
        """Report for one month compared with the month before.

        Args:
            year: Calendar year.
            month: 1-12.
            app_id: Restrict to one app; None covers all apps and adds a per-app breakdown.
            top_territories: How many territories to list by downloads; must be positive.
        """
        if not 1 <= month <= 12:
            raise ValueError("month must be between 1 and 12")
        if top_territories < 1:
            raise ValueError("top_territories must be positive")
        apps = {a.id: a for a in await self._catalog.list_apps()}
        if app_id is not None and app_id not in apps:
            raise NotFoundError(f"App {app_id!r} not found")

        current_rows = await self._rows(year, month, app_id)
        previous_year, previous_month = _previous_month(year, month)
        previous_rows = await self._rows(previous_year, previous_month, app_id)
        current = self._summarize(current_rows, year, month, self._today())
        previous = self._summarize(previous_rows, previous_year, previous_month, self._today())

        return MonthlyReport(
            app=apps[app_id] if app_id else None,
            current=current,
            previous=previous,
            downloads_change_pct=_change_pct(current.downloads, previous.downloads),
            top_territories=self._top_territories(current_rows, top_territories),
            by_app=[] if app_id else self._by_app(current_rows, apps),
        )

    async def _rows(self, year: int, month: int, app_id: str | None) -> list[SalesRow]:
        start, end = _month_bounds(year, month)
        rows = await self._sales.list_sales(start, end)
        return [r for r in rows if app_id is None or r.app_id == app_id]

    @staticmethod
    def _summarize(rows: list[SalesRow], year: int, month: int, today: date) -> MonthSummary:
        last_day = max((r.day for r in rows), default=None)
        return MonthSummary(
            year=year,
            month=month,
            downloads=_units(rows, SalesKind.DOWNLOAD),
            updates=_units(rows, SalesKind.UPDATE),
            in_app_purchases=_units(rows, SalesKind.IN_APP_PURCHASE),
            proceeds=_proceeds_by_currency(rows),
            last_data_day=last_day,
            is_partial=_month_bounds(year, month)[1] >= today,
        )

    @staticmethod
    def _top_territories(rows: list[SalesRow], limit: int) -> list[TerritoryTotal]:
        totals: Counter[str] = Counter()
        for row in rows:
            if row.kind is SalesKind.DOWNLOAD:
                totals[row.territory] += row.units
        ranked = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
        return [TerritoryTotal(territory=t, downloads=n) for t, n in ranked[:limit]]

    @staticmethod
    def _by_app(rows: list[SalesRow], apps: dict[str, App]) -> list[AppTotal]:
        totals = [
            AppTotal(
                app=app,
                downloads=_units(app_rows, SalesKind.DOWNLOAD),
                proceeds=_proceeds_by_currency(app_rows),
            )
            for app in apps.values()
            if (app_rows := [r for r in rows if r.app_id == app.id])
        ]
        return sorted(totals, key=lambda t: (-t.downloads, t.app.name))


def _change_pct(current: int, previous: int) -> float | None:
    if previous == 0:
        return None
    return round((current - previous) / previous * 100, 1)
