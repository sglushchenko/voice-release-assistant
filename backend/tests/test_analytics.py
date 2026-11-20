from datetime import date
from decimal import Decimal

import pytest

from release_assistant.application.analytics import AnalyticsService
from release_assistant.application.reporting import render_markdown
from release_assistant.domain.entities import SalesKind, SalesRow
from release_assistant.domain.errors import NotFoundError
from release_assistant.infrastructure.fixtures import sample_gateway
from release_assistant.infrastructure.mock_gateway import MockAppStoreGateway

TODAY = date(2026, 11, 20)


def service(gateway: MockAppStoreGateway | None = None) -> AnalyticsService:
    gateway = gateway or sample_gateway()
    return AnalyticsService(gateway, gateway, today=lambda: TODAY)


async def test_downloads_exclude_updates_and_compare_with_previous_month() -> None:
    report = await service().monthly_report(2026, 11)

    assert report.current.downloads == 240
    assert report.current.updates == 250
    assert report.current.in_app_purchases == 35
    assert report.previous.downloads == 200
    assert report.downloads_change_pct == 20.0


async def test_proceeds_are_never_summed_across_currencies() -> None:
    report = await service().monthly_report(2026, 11)

    assert report.current.proceeds == {
        "EUR": Decimal("10.00"),
        "GBP": Decimal("15.00"),
        "USD": Decimal("70.00"),
    }


async def test_report_for_one_app_ignores_other_apps_and_has_no_breakdown() -> None:
    report = await service().monthly_report(2026, 11, app_id="app-1")

    assert report.app is not None
    assert report.app.name == "Pocket Notes"
    assert report.current.downloads == 180
    assert report.previous.downloads == 160
    assert report.downloads_change_pct == 12.5
    assert report.by_app == []


async def test_all_apps_report_breaks_down_by_app_best_first() -> None:
    report = await service().monthly_report(2026, 11)

    assert [(a.app.id, a.downloads) for a in report.by_app] == [("app-1", 180), ("app-2", 60)]


async def test_top_territories_are_ranked_and_limited() -> None:
    report = await service().monthly_report(2026, 11, top_territories=2)

    assert [(t.territory, t.downloads) for t in report.top_territories] == [
        ("USA", 150),
        ("GBR", 60),
    ]


async def test_current_month_is_partial_and_a_finished_month_is_not() -> None:
    november = await service().monthly_report(2026, 11)
    october = await service().monthly_report(2026, 10)

    assert november.current.is_partial is True
    assert november.current.last_data_day == date(2026, 11, 18)
    assert october.current.is_partial is False


async def test_january_compares_with_december_of_the_previous_year() -> None:
    report = await service().monthly_report(2027, 1)

    assert (report.previous.year, report.previous.month) == (2026, 12)


async def test_no_percentage_when_previous_month_had_no_downloads() -> None:
    report = await service().monthly_report(2026, 10)

    assert report.previous.downloads == 0
    assert report.downloads_change_pct is None


async def test_empty_month_gives_zero_totals() -> None:
    gateway = MockAppStoreGateway()

    report = await service(gateway).monthly_report(2026, 11)

    assert report.current.downloads == 0
    assert report.current.proceeds == {}
    assert report.current.last_data_day is None


@pytest.mark.parametrize("month", [0, 13])
async def test_invalid_month_is_rejected(month: int) -> None:
    with pytest.raises(ValueError, match="month"):
        await service().monthly_report(2026, month)


async def test_unknown_app_raises_not_found() -> None:
    with pytest.raises(NotFoundError):
        await service().monthly_report(2026, 11, app_id="missing")


async def test_non_positive_territory_limit_is_rejected() -> None:
    with pytest.raises(ValueError, match="top_territories"):
        await service().monthly_report(2026, 11, top_territories=0)


def test_sales_rows_reject_negative_units() -> None:
    with pytest.raises(ValueError):
        SalesRow(app_id="a", day=TODAY, territory="USA", kind=SalesKind.DOWNLOAD, units=-1)


async def test_markdown_report_states_estimate_and_partial_data() -> None:
    text = render_markdown(await service().monthly_report(2026, 11))

    assert text.startswith("# November 2026: All apps\n")
    assert "Estimated figures from Sales and Trends" in text
    assert "Data is partial (last day with data: 2026-11-18)" in text
    assert "- Downloads: 240 (+20.0% vs October 2026: 200)" in text
    assert "- Estimated proceeds: 10.00 EUR, 15.00 GBP, 70.00 USD" in text
    assert "| USA | 150 |" in text
    assert "| Pocket Notes | 180 | 10.00 EUR, 70.00 USD |" in text


async def test_markdown_for_one_complete_month_omits_breakdown_and_partial_note() -> None:
    text = render_markdown(await service().monthly_report(2026, 10, app_id="app-1"))

    assert text.startswith("# October 2026: Pocket Notes\n")
    assert "partial" not in text
    assert "## By app" not in text
    assert "no comparison available" in text
