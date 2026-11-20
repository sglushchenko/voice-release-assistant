"""Entities the assistant works with.

Deliberately a small subset of App Store Connect: only fields the agent needs to answer
questions or act. Wire-format details (JSON:API relationships, pagination) belong to the
infrastructure adapter, not here.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Entity(BaseModel):
    """Immutable base so entities can be shared safely between tools, logs and evals."""

    model_config = ConfigDict(frozen=True)


class BuildProcessingState(StrEnum):
    PROCESSING = "PROCESSING"
    VALID = "VALID"
    FAILED = "FAILED"
    INVALID = "INVALID"


class AppStoreVersionState(StrEnum):
    PREPARE_FOR_SUBMISSION = "PREPARE_FOR_SUBMISSION"
    WAITING_FOR_REVIEW = "WAITING_FOR_REVIEW"
    IN_REVIEW = "IN_REVIEW"
    REJECTED = "REJECTED"
    PENDING_DEVELOPER_RELEASE = "PENDING_DEVELOPER_RELEASE"
    READY_FOR_SALE = "READY_FOR_SALE"


class ReviewResponseState(StrEnum):
    PENDING_PUBLISH = "PENDING_PUBLISH"
    PUBLISHED = "PUBLISHED"


class SalesKind(StrEnum):
    """Transaction types the assistant distinguishes. Updates are always free and must not
    be counted as downloads."""

    DOWNLOAD = "download"
    UPDATE = "update"
    IN_APP_PURCHASE = "in_app_purchase"


class SalesRow(Entity):
    """One day of one kind of transaction in one territory.

    `proceeds` is the developer's total for the row (not per unit), in `currency`, which
    follows the territory. Amounts are estimates from Sales and Trends; never sum across
    currencies.
    """

    app_id: str
    day: date
    territory: str
    kind: SalesKind
    units: int = Field(ge=0)
    proceeds: Decimal = Decimal(0)
    currency: str = "USD"


class EngagementRow(Entity):
    """One day of App Store funnel counts for one app in one territory.

    `downloads` here come from App Store Analytics and need not equal the units in Sales and
    Trends (different time zones and rules); label them by source when showing both.
    """

    app_id: str
    day: date
    territory: str
    impressions: int = Field(ge=0)
    product_page_views: int = Field(ge=0)
    downloads: int = Field(ge=0)


class DateCoverage(Entity):
    """Days the source has published. Apple generates reports with a delay and may keep only
    a limited window, so a day without rows means "no activity" only inside this range."""

    first_day: date | None = None
    last_day: date | None = None

    def covers(self, start: date, end: date) -> bool:
        return (
            self.first_day is not None
            and self.last_day is not None
            and self.first_day <= start
            and end <= self.last_day
        )


class App(Entity):
    id: str
    name: str
    bundle_id: str


class Build(Entity):
    id: str
    app_id: str
    version: str
    build_number: str
    processing_state: BuildProcessingState
    uploaded_at: datetime


class AppStoreVersion(Entity):
    id: str
    app_id: str
    version_string: str
    state: AppStoreVersionState


class BetaGroup(Entity):
    id: str
    app_id: str
    name: str


class BetaTester(Entity):
    id: str
    email: str
    first_name: str | None = None
    last_name: str | None = None


class ReviewResponse(Entity):
    id: str
    review_id: str
    body: str
    state: ReviewResponseState


class CustomerReview(Entity):
    """A public review. `title` and `body` are written by third parties and must be treated
    as untrusted input everywhere they are passed to a model (see ADR 0003)."""

    id: str
    app_id: str
    rating: int = Field(ge=1, le=5)
    title: str
    body: str
    territory: str
    created_at: datetime
    response: ReviewResponse | None = None
