"""Sales and Trends over a fake service that serves gzip tab-separated reports."""

import gzip
from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest
import structlog

from asc_support import APPS, make_client, make_gateway, not_found
from release_assistant.application.analytics import AnalyticsService
from release_assistant.domain.entities import SalesKind
from release_assistant.domain.errors import UpstreamError
from release_assistant.infrastructure.appstoreconnect.sales import SalesReader

HEADER = [
    "Provider", "SKU", "Title", "Product Type Identifier", "Units", "Developer Proceeds",
    "Country Code", "Currency of Proceeds", "Apple Identifier", "Parent Identifier",
]  # fmt: skip
DAY = date(2026, 11, 10)


def record(
    product: str, units: int, proceeds: str, country: str = "US", currency: str = "USD",
    apple_id: str = "1001", parent: str = "",
) -> list[str]:  # fmt: skip
    return ["APPLE", "sku", "t", product, str(units), proceeds, country, currency, apple_id, parent]


def report(*records: list[str], header: list[str] = HEADER) -> bytes:
    lines = ["\t".join(header), *("\t".join(r) for r in records)]
    return gzip.compress(("\n".join(lines) + "\n").encode())


SKUS = {"data": [{"type": "apps", "id": "1001", "attributes": {"sku": "NOTES"}}]}


def reader(routes: dict[str, bytes | httpx.Response], today: date = date(2026, 11, 20)):  # type: ignore[no-untyped-def]
    """A SalesReader over a fake service; `routes` maps a report date to its answer."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/apps":
            return httpx.Response(200, json={**SKUS, "links": {}})
        answer = routes.get(request.url.params.get("filter[reportDate]", ""), not_found())
        return httpx.Response(200, content=answer) if isinstance(answer, bytes) else answer

    return SalesReader(make_client(handler), "88000001", clock=lambda: today), seen


async def test_the_request_names_the_daily_summary_for_the_vendor() -> None:
    sales, seen = reader({DAY.isoformat(): report(record("1", 3, "0.00"))})

    await sales.list_sales(DAY, DAY)

    params = next(r for r in seen if r.url.path == "/v1/salesReports").url.params
    assert (params["filter[vendorNumber]"], params["filter[reportType]"]) == ("88000001", "SALES")
    assert (params["filter[reportSubType]"], params["filter[frequency]"]) == ("SUMMARY", "DAILY")
    assert params["filter[reportDate]"] == DAY.isoformat()


async def test_rows_are_classified_and_proceeds_are_units_times_the_per_unit_amount() -> None:
    body = report(
        record("1", 4, "0.00"),  # a free download
        record("7", 10, "0.00"),  # updates
        record("IAY", 2, "2.10", country="DE", currency="EUR", parent="NOTES", apple_id="9"),
    )
    sales, _ = reader({DAY.isoformat(): body})

    rows = await sales.list_sales(DAY, DAY)

    by_kind = {(r.kind, r.territory): r for r in rows}
    assert by_kind[(SalesKind.DOWNLOAD, "US")].units == 4
    assert by_kind[(SalesKind.UPDATE, "US")].units == 10
    purchase = by_kind[(SalesKind.IN_APP_PURCHASE, "DE")]
    assert (purchase.app_id, purchase.units) == ("1001", 2)  # attributed via the parent SKU
    assert (purchase.proceeds, purchase.currency) == (Decimal("4.20"), "EUR")


async def test_rows_of_the_same_kind_territory_and_currency_are_merged() -> None:
    body = report(record("1", 2, "0"), record("1F", 3, "0"), record("1T", 1, "0"))
    sales, _ = reader({DAY.isoformat(): body})

    rows = await sales.list_sales(DAY, DAY)

    assert [(r.kind, r.units) for r in rows] == [(SalesKind.DOWNLOAD, 6)]


async def test_redownloads_and_unknown_product_types_stay_out_of_the_totals_and_are_logged() -> (
    None
):
    sales, _ = reader({DAY.isoformat(): report(record("3", 50, "0"), record("ZZ9", 5, "0"))})

    with structlog.testing.capture_logs() as logs:
        rows = await sales.list_sales(DAY, DAY)

    assert rows == []
    assert any(e["event"] == "asc.sales_rows_left_out" and e["rows"] == 2 for e in logs)


async def test_refunds_cannot_make_units_negative() -> None:
    # A refund row has negative units and the usual per-unit price.
    sales, _ = reader({DAY.isoformat(): report(record("IA1", -2, "1.00", parent="NOTES"))})

    rows = await sales.list_sales(DAY, DAY)

    assert (rows[0].units, rows[0].proceeds) == (0, Decimal(0))


async def test_a_day_without_a_report_is_empty_not_an_error() -> None:
    sales, _ = reader({})

    assert await sales.list_sales(DAY, DAY + timedelta(days=2)) == []


async def test_several_days_come_back_oldest_first() -> None:
    routes = {
        (DAY + timedelta(days=n)).isoformat(): report(record("1", n + 1, "0")) for n in range(3)
    }
    sales, _ = reader(routes)

    rows = await sales.list_sales(DAY, DAY + timedelta(days=2))

    assert [(r.day, r.units) for r in rows] == [(DAY + timedelta(days=n), n + 1) for n in range(3)]


async def test_settled_days_are_fetched_once_but_recent_days_every_time() -> None:
    old, recent = date(2026, 11, 1), date(2026, 11, 19)
    sales, seen = reader({d.isoformat(): report(record("1", 1, "0")) for d in (old, recent)})

    for _ in range(2):
        await sales.list_sales(old, old)
        await sales.list_sales(recent, recent)

    reports = [r for r in seen if r.url.path == "/v1/salesReports"]
    assert [r.url.params["filter[reportDate]"] for r in reports] == [
        "2026-11-01",
        "2026-11-19",
        "2026-11-19",
    ]


async def test_a_longer_period_than_two_months_is_refused() -> None:
    sales, _ = reader({})

    with pytest.raises(UpstreamError, match="at most"):
        await sales.list_sales(DAY, DAY + timedelta(days=90))


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        (b"not gzip at all", "unreadable"),
        (report(record("1", 1, "0"), header=["Only", "Two"]), "format changed"),
        (report(record("1", "x", "0")), "cannot be read"),  # type: ignore[arg-type]
        (gzip.compress(b"x" * 10 + b"\n" * 0), "format changed"),
    ],
)
async def test_a_broken_report_is_an_upstream_error_that_leaks_nothing(
    body: bytes, fragment: str
) -> None:
    sales, _ = reader({DAY.isoformat(): body})

    with pytest.raises(UpstreamError, match=fragment) as raised:
        await sales.list_sales(DAY, DAY)

    assert "x" * 10 not in str(raised.value)


async def test_a_report_that_inflates_beyond_the_ceiling_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "release_assistant.infrastructure.appstoreconnect.sales._MAX_REPORT_BYTES", 64
    )
    sales, _ = reader({DAY.isoformat(): gzip.compress(b"a" * 10_000)})

    with pytest.raises(UpstreamError, match="larger than"):
        await sales.list_sales(DAY, DAY)


async def test_a_role_without_sales_access_gets_the_role_message() -> None:
    denied = httpx.Response(403, json={"errors": [{"status": "403", "code": "FORBIDDEN"}]})
    sales, _ = reader({DAY.isoformat(): denied})

    with pytest.raises(UpstreamError, match="role does not allow"):
        await sales.list_sales(DAY, DAY)


async def test_the_monthly_report_works_over_real_sales_rows() -> None:
    sales, _ = reader(
        {
            date(2026, 10, 15).isoformat(): report(record("1", 7, "0")),
            date(2026, 9, 15).isoformat(): report(record("1", 5, "0")),
        }
    )
    catalog = make_gateway(lambda _: httpx.Response(200, json=APPS))

    monthly = await AnalyticsService(sales, catalog).monthly_report(2026, 10, app_id="1001")

    assert (monthly.current.downloads, monthly.previous.downloads) == (7, 5)
