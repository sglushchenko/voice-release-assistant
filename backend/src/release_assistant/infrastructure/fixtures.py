"""Shared sample data. Evals and tests reuse it so results stay comparable across runs."""

from datetime import UTC, date, datetime
from decimal import Decimal

from release_assistant.domain.entities import (
    App,
    AppStoreVersion,
    AppStoreVersionState,
    BetaGroup,
    BetaTester,
    Build,
    BuildProcessingState,
    CustomerReview,
    SalesKind,
    SalesRow,
)
from release_assistant.infrastructure.mock_gateway import MockAppStoreGateway

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def _sample_sales() -> list[SalesRow]:
    """October 2026 is complete; November stops at the 18th, as real data would on the 20th.
    Proceeds are in the territory's currency, so totals must stay split by currency."""
    d, i, u = SalesKind.DOWNLOAD, SalesKind.IN_APP_PURCHASE, SalesKind.UPDATE

    def row(
        app_id: str, day: date, territory: str, kind: SalesKind, units: int, proceeds: str, cur: str
    ) -> SalesRow:
        return SalesRow(
            app_id=app_id,
            day=day,
            territory=territory,
            kind=kind,
            units=units,
            proceeds=Decimal(proceeds),
            currency=cur,
        )

    return [
        row("app-1", date(2026, 10, 10), "USA", d, 120, "0", "USD"),
        row("app-1", date(2026, 10, 10), "USA", i, 20, "55.00", "USD"),
        row("app-1", date(2026, 10, 12), "DEU", d, 40, "0", "EUR"),
        row("app-1", date(2026, 10, 12), "DEU", i, 5, "12.50", "EUR"),
        row("app-1", date(2026, 10, 15), "USA", u, 300, "0", "USD"),
        row("app-2", date(2026, 10, 20), "GBR", d, 40, "0", "GBP"),
        row("app-2", date(2026, 10, 20), "GBR", i, 4, "9.00", "GBP"),
        row("app-1", date(2026, 11, 5), "USA", d, 150, "0", "USD"),
        row("app-1", date(2026, 11, 5), "USA", i, 25, "70.00", "USD"),
        row("app-1", date(2026, 11, 9), "DEU", d, 30, "0", "EUR"),
        row("app-1", date(2026, 11, 9), "DEU", i, 4, "10.00", "EUR"),
        row("app-1", date(2026, 11, 14), "USA", u, 250, "0", "USD"),
        row("app-2", date(2026, 11, 18), "GBR", d, 60, "0", "GBP"),
        row("app-2", date(2026, 11, 18), "GBR", i, 6, "15.00", "GBP"),
    ]


def sample_gateway() -> MockAppStoreGateway:
    """Fresh gateway per call; mutations in one test never leak into another."""
    return MockAppStoreGateway(
        apps=[
            App(id="app-1", name="Pocket Notes", bundle_id="com.example.pocketnotes"),
            App(id="app-2", name="Habit Garden", bundle_id="com.example.habitgarden"),
        ],
        builds=[
            Build(
                id="build-1",
                app_id="app-1",
                version="1.4.0",
                build_number="41",
                processing_state=BuildProcessingState.VALID,
                uploaded_at=datetime(2026, 11, 18, 9, 0, tzinfo=UTC),
            ),
            Build(
                id="build-2",
                app_id="app-1",
                version="1.4.0",
                build_number="42",
                processing_state=BuildProcessingState.PROCESSING,
                uploaded_at=datetime(2026, 11, 19, 15, 30, tzinfo=UTC),
            ),
            Build(
                id="build-3",
                app_id="app-2",
                version="2.0.0",
                build_number="7",
                processing_state=BuildProcessingState.FAILED,
                uploaded_at=datetime(2026, 11, 17, 8, 15, tzinfo=UTC),
            ),
        ],
        versions=[
            AppStoreVersion(
                id="ver-1",
                app_id="app-1",
                version_string="1.4.0",
                state=AppStoreVersionState.WAITING_FOR_REVIEW,
            ),
            AppStoreVersion(
                id="ver-2",
                app_id="app-2",
                version_string="1.9.2",
                state=AppStoreVersionState.READY_FOR_SALE,
            ),
            AppStoreVersion(
                id="ver-3",
                app_id="app-2",
                version_string="2.0.0",
                state=AppStoreVersionState.REJECTED,
            ),
        ],
        groups=[
            BetaGroup(id="group-1", app_id="app-1", name="Internal"),
            BetaGroup(id="group-2", app_id="app-1", name="Public Beta"),
        ],
        testers=[
            BetaTester(id="tester-a", email="anna@example.com", first_name="Anna"),
            BetaTester(id="tester-b", email="boris@example.com", first_name="Boris"),
        ],
        memberships={"group-1": ["tester-a"], "group-2": ["tester-a", "tester-b"]},
        sales=_sample_sales(),
        reviews=[
            CustomerReview(
                id="review-1",
                app_id="app-1",
                rating=5,
                title="Love it",
                body="Fast and simple.",
                territory="USA",
                created_at=datetime(2026, 11, 19, 10, 0, tzinfo=UTC),
            ),
            CustomerReview(
                id="review-2",
                app_id="app-1",
                rating=1,
                title="Crashes on launch",
                body="Since the last update the app crashes when I open it.",
                territory="DEU",
                created_at=datetime(2026, 11, 18, 18, 45, tzinfo=UTC),
            ),
            CustomerReview(
                id="review-3",
                app_id="app-1",
                rating=2,
                title="Sync is slow",
                body="Notes take minutes to sync between devices.",
                territory="GBR",
                created_at=datetime(2026, 11, 10, 7, 20, tzinfo=UTC),
            ),
        ],
    )
