from datetime import date

import pytest

from release_assistant.application.engagement import EngagementService
from release_assistant.application.reporting import render_engagement_markdown
from release_assistant.domain.entities import DateCoverage
from release_assistant.domain.errors import DataUnavailableError, NotFoundError
from release_assistant.domain.ports import EngagementReports
from release_assistant.infrastructure.fixtures import sample_gateway
from release_assistant.infrastructure.mock_gateway import MockAppStoreGateway

START, END = date(2026, 11, 1), date(2026, 11, 14)


def service(gateway: MockAppStoreGateway | None = None) -> EngagementService:
    gateway = gateway or sample_gateway()
    return EngagementService(gateway, gateway)


def test_mock_satisfies_the_engagement_port() -> None:
    assert isinstance(sample_gateway(), EngagementReports)


async def test_totals_and_comparison_with_the_previous_equal_period() -> None:
    report = await service().report(START, END)

    assert (report.totals.impressions, report.totals.product_page_views) == (22000, 1700)
    assert report.totals.downloads == 290
    assert (report.previous_start, report.previous_end) == (date(2026, 10, 18), date(2026, 10, 31))
    assert report.previous.impressions == 11000
    assert report.changes.impressions == 100.0
    assert report.changes.product_page_views == 93.2
    assert report.changes.downloads == 123.1


async def test_funnel_rates() -> None:
    report = await service().report(START, END)

    assert report.tap_through_pct == 7.7
    assert report.conversion_pct == 17.1


async def test_single_app_ignores_other_apps_and_has_no_breakdown() -> None:
    report = await service().report(START, END, app_id="app-1")

    assert report.totals.impressions == 20000
    assert report.previous.impressions == 10000
    assert report.by_app == []


async def test_all_apps_breakdown_is_ranked_by_impressions() -> None:
    report = await service().report(START, END)

    assert [(a.app.id, a.totals.impressions) for a in report.by_app] == [
        ("app-1", 20000),
        ("app-2", 2000),
    ]


async def test_daily_series_is_zero_filled_for_every_day() -> None:
    report = await service().report(START, END)

    assert len(report.daily) == 14
    by_day = {d.day: d.totals.impressions for d in report.daily}
    assert by_day[date(2026, 11, 5)] == 10000
    assert by_day[date(2026, 11, 6)] == 0


async def test_period_inside_coverage_is_complete() -> None:
    report = await service().report(START, END)

    assert report.is_complete is True
    assert report.previous_is_complete is True


async def test_period_beyond_coverage_is_flagged_incomplete() -> None:
    report = await service().report(date(2026, 11, 10), date(2026, 11, 20))

    assert report.is_complete is False


async def test_comparison_period_outside_the_published_window_is_flagged() -> None:
    report = await service().report(date(2026, 10, 25), date(2026, 11, 7))

    assert report.is_complete is True
    assert report.previous_is_complete is False


async def test_recent_ends_at_the_latest_published_day_not_today() -> None:
    report = await service().recent(7)

    assert (report.start, report.end) == (date(2026, 11, 12), date(2026, 11, 18))
    assert report.totals.impressions == 6000


async def test_recent_before_any_report_exists_raises() -> None:
    with pytest.raises(DataUnavailableError):
        await service(MockAppStoreGateway()).recent(7)


async def test_zero_previous_period_gives_no_percentage() -> None:
    report = await service().report(date(2026, 10, 17), date(2026, 10, 18))

    assert report.previous.impressions == 0
    assert report.changes.impressions is None
    assert report.conversion_pct is None


async def test_validation_and_unknown_app() -> None:
    with pytest.raises(ValueError, match="end"):
        await service().report(END, START)
    with pytest.raises(ValueError, match="days"):
        await service().recent(0)
    with pytest.raises(NotFoundError):
        await service().report(START, END, app_id="missing")


def test_coverage_requires_both_ends() -> None:
    assert DateCoverage().covers(START, END) is False
    assert DateCoverage(first_day=START, last_day=END).covers(START, END) is True


async def test_markdown_states_source_comparison_and_rates() -> None:
    text = render_engagement_markdown(await service().report(START, END))

    assert text.startswith("# Engagement 2026-11-01 to 2026-11-14: All apps\n")
    assert "Source: App Store Analytics" in text
    assert "Incomplete" not in text
    assert "- Impressions: 22,000 (+100.0% vs 2026-10-18 to 2026-10-31: 11,000)" in text
    assert "- Tap-through (page views / impressions): 7.7%" in text
    assert "| Pocket Notes | 20,000 | 1,600 | 270 |" in text


async def test_markdown_warns_about_incomplete_data() -> None:
    incomplete = await service().report(date(2026, 11, 10), date(2026, 11, 20))
    old_comparison = await service().report(date(2026, 10, 25), date(2026, 11, 7))

    assert "Incomplete: published data covers 2026-10-17 to 2026-11-18" in (
        render_engagement_markdown(incomplete)
    )
    assert "comparison period is not fully published" in render_engagement_markdown(old_comparison)
