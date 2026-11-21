"""Sales and Trends from App Store Connect: first-time downloads, updates, purchases, proceeds.

Apple serves one gzip-compressed, tab-separated report per day. Everything here is an estimate
(Sales and Trends, not the settled Finance reports) and Apple publishes with a delay, so a day
with no report is "nothing published", not a proven zero.
"""

import asyncio
import csv
import io
import zlib
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import structlog

from release_assistant.domain.entities import SalesKind, SalesRow
from release_assistant.domain.errors import NotFoundError, UpstreamError
from release_assistant.infrastructure.appstoreconnect.client import AscClient

log = structlog.get_logger(__name__)

# Product Type Identifier column, from Apple's Sales and Trends reporting documentation.
# Anything else (re-downloads, bundles, other platforms' codes) is deliberately left out of the
# totals and counted in the log, rather than guessed into a category.
_DOWNLOADS = frozenset({"1", "1F", "1T", "F1", "1E", "1EP", "1EU"})
_UPDATES = frozenset({"7", "7F", "7T", "F7"})
_PURCHASES = frozenset({"IA1", "IA9", "IAY", "IAC", "FI1"})

_REQUIRED_COLUMNS = (
    "SKU",
    "Parent Identifier",
    "Apple Identifier",
    "Product Type Identifier",
    "Units",
    "Developer Proceeds",
    "Country Code",
    "Currency of Proceeds",
)
_MAX_REPORT_BYTES = 50 * 1024 * 1024  # a day of a very large account is far smaller
_FINAL_AFTER_DAYS = 3  # Apple has published a day's report by then; earlier ones may change
_MAX_DAYS = 70  # a month and the one before it, with room to spare
_PARALLEL = 5


class SalesReader:
    """SalesReports over the real service.

    Args:
        client: The authenticated HTTP client.
        vendor_number: The account's vendor number (Payments and Financial Reports); Apple
            requires it for every sales request.
        clock: Today, injectable so tests need not depend on the date.
    """

    def __init__(
        self,
        client: AscClient,
        vendor_number: str,
        *,
        clock: Callable[[], date] = lambda: datetime.now(UTC).date(),
    ) -> None:
        self._client = client
        self._vendor = vendor_number
        self._clock = clock
        self._final: dict[date, list[SalesRow]] = {}
        self._apps_by_sku: dict[str, str] | None = None

    async def list_sales(self, start: date, end: date) -> list[SalesRow]:
        if (end - start).days + 1 > _MAX_DAYS:
            raise UpstreamError(f"sales can be read for at most {_MAX_DAYS} days at a time")
        days = [start + timedelta(days=n) for n in range((end - start).days + 1)]
        gate = asyncio.Semaphore(_PARALLEL)

        async def one(day: date) -> list[SalesRow]:
            async with gate:
                return await self._day(day)

        rows = [row for part in await asyncio.gather(*(one(d) for d in days)) for row in part]
        return sorted(rows, key=lambda r: r.day)

    async def _day(self, day: date) -> list[SalesRow]:
        if day in self._final:
            return self._final[day]
        try:
            raw = await self._client.get_bytes(
                "/v1/salesReports",
                {
                    "filter[vendorNumber]": self._vendor,
                    "filter[reportType]": "SALES",
                    "filter[reportSubType]": "SUMMARY",
                    "filter[frequency]": "DAILY",
                    "filter[reportDate]": day.isoformat(),
                    "filter[version]": "1_1",
                },
            )
        except NotFoundError:
            return []  # nothing published for that day (yet)
        rows = _parse_report(_gunzip(raw), day, await self._skus())
        if day <= self._clock() - timedelta(days=_FINAL_AFTER_DAYS):
            self._final[day] = rows
        return rows

    async def _skus(self) -> dict[str, str]:
        """App SKU to app id: in-app purchases name their parent app only by SKU."""
        if self._apps_by_sku is None:
            data, _ = await self._client.get_all("/v1/apps", {"fields[apps]": "sku", "limit": 200})
            self._apps_by_sku = {
                str(r.get("attributes", {}).get("sku")): str(r["id"])
                for r in data
                if r.get("attributes", {}).get("sku")
            }
        return self._apps_by_sku


def _gunzip(raw: bytes) -> str:
    """Decompress with a ceiling, so a hostile or corrupt response cannot exhaust memory."""
    inflater = zlib.decompressobj(wbits=31)
    try:
        text = inflater.decompress(raw, _MAX_REPORT_BYTES)
    except zlib.error:
        raise UpstreamError("App Store Connect returned an unreadable sales report") from None
    if inflater.unconsumed_tail:
        raise UpstreamError("the sales report is larger than the allowed size")
    return text.decode("utf-8", errors="replace")


def _parse_report(text: str, day: date, apps_by_sku: dict[str, str]) -> list[SalesRow]:
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    missing = [c for c in _REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise UpstreamError(f"the sales report format changed: missing columns {missing}")

    units: dict[tuple[str, str, SalesKind, str], int] = defaultdict(int)
    proceeds: dict[tuple[str, str, SalesKind, str], Decimal] = defaultdict(Decimal)
    skipped = 0
    for record in reader:
        product = (record["Product Type Identifier"] or "").strip()
        kind = _kind(product)
        if kind is None:
            skipped += 1
            continue
        app_id = (
            apps_by_sku.get((record["Parent Identifier"] or "").strip())
            if kind is SalesKind.IN_APP_PURCHASE
            else (record["Apple Identifier"] or "").strip()
        )
        if not app_id:
            skipped += 1
            continue
        try:
            count = int(record["Units"])
            per_unit = Decimal(record["Developer Proceeds"] or "0")
        except (ValueError, InvalidOperation):
            raise UpstreamError("the sales report contains a number that cannot be read") from None
        key = (app_id, record["Country Code"].strip(), kind, record["Currency of Proceeds"].strip())
        units[key] += count
        proceeds[key] += per_unit * count
    if skipped:
        log.info("asc.sales_rows_left_out", day=day.isoformat(), rows=skipped)

    rows: list[SalesRow] = []
    for key, count in sorted(units.items(), key=lambda item: item[0][:3]):
        app_id, territory, kind, currency = key
        if count < 0:
            # Refunds can outweigh a day's sales in one territory; the entity counts only
            # what was sold, so net the day at zero rather than report negative units.
            log.warning("asc.sales_net_negative", day=day.isoformat(), kind=kind.value)
        rows.append(
            SalesRow(
                app_id=app_id,
                day=day,
                territory=territory,
                kind=kind,
                units=max(count, 0),
                proceeds=max(proceeds[key], Decimal(0)),
                currency=currency or "USD",
            )
        )
    return rows


def _kind(product_type: str) -> SalesKind | None:
    if product_type in _DOWNLOADS:
        return SalesKind.DOWNLOAD
    if product_type in _UPDATES:
        return SalesKind.UPDATE
    if product_type in _PURCHASES:
        return SalesKind.IN_APP_PURCHASE
    return None
