"""Sales and engagement reports (read-only)."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from release_assistant.api.deps import ApplicationDep, limit, require_scope
from release_assistant.api.errors import ApiError
from release_assistant.application.analytics import MonthlyReport
from release_assistant.application.engagement import EngagementReport

router = APIRouter(
    prefix="/v1/reports",
    dependencies=[Depends(require_scope("read")), Depends(limit("reports"))],
    tags=["reports"],
)

_MAX_PERIOD_DAYS = 366


@router.get("/sales/monthly")
async def monthly_sales(
    application: ApplicationDep,
    year: Annotated[int, Query(ge=2007, le=2100)],
    month: Annotated[int, Query(ge=1, le=12)],
    app_id: str | None = None,
) -> MonthlyReport:
    """Estimated downloads and proceeds from Sales and Trends, compared with the prior month."""
    return await application.analytics.monthly_report(year, month, app_id=app_id)


@router.get("/engagement")
async def engagement(
    application: ApplicationDep, start: date, end: date, app_id: str | None = None
) -> EngagementReport:
    """Impressions, page views and downloads for a period; check `is_complete` before trusting
    the figures."""
    if end < start:
        raise ApiError(422, "invalid_period", "end must not be before start")
    if (end - start).days >= _MAX_PERIOD_DAYS:
        raise ApiError(422, "invalid_period", f"Period must not exceed {_MAX_PERIOD_DAYS} days")
    return await application.engagement.report(start, end, app_id=app_id)


@router.get("/engagement/recent")
async def recent_engagement(
    application: ApplicationDep,
    days: Annotated[int, Query(ge=1, le=90)] = 7,
    app_id: str | None = None,
) -> EngagementReport:
    """The last `days` days of published data, ending at the latest published day."""
    return await application.engagement.recent(days, app_id=app_id)
