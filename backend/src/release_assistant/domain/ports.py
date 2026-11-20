"""Ports: what the application layer needs from App Store Connect.

Split by capability (interface segregation) so a use case depends only on what it uses,
and a test double stays tiny. Implementations: in-memory mock now, real HTTP adapter later.
Risk tiers and confirmation are NOT decided here; see ADR 0002.
"""

from datetime import datetime
from typing import Protocol, runtime_checkable

from release_assistant.domain.entities import (
    App,
    AppStoreVersion,
    BetaGroup,
    BetaTester,
    Build,
    CustomerReview,
    ReviewResponse,
)


@runtime_checkable
class AppCatalog(Protocol):
    """Read-only view of apps and their store versions."""

    async def list_apps(self) -> list[App]: ...

    async def list_versions(self, app_id: str) -> list[AppStoreVersion]:
        """Raises NotFoundError for an unknown app."""
        ...


@runtime_checkable
class BuildCatalog(Protocol):
    """Read-only view of uploaded builds."""

    async def list_builds(self, app_id: str) -> list[Build]:
        """Newest first. Raises NotFoundError for an unknown app."""
        ...


@runtime_checkable
class TesterDirectory(Protocol):
    """TestFlight groups and their testers."""

    async def list_groups(self, app_id: str) -> list[BetaGroup]: ...

    async def get_group(self, group_id: str) -> BetaGroup:
        """Raises NotFoundError for an unknown group."""
        ...

    async def list_testers(self, group_id: str) -> list[BetaTester]: ...

    async def add_tester(
        self,
        group_id: str,
        email: str,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> BetaTester:
        """Raises NotFoundError for an unknown group, AlreadyExistsError if already a member."""
        ...

    async def remove_tester(self, group_id: str, tester_id: str) -> None:
        """Removes membership only. Raises NotFoundError if the tester is not in the group."""
        ...


@runtime_checkable
class ReviewInbox(Protocol):
    """Customer reviews and developer responses."""

    async def get_review(self, review_id: str) -> CustomerReview:
        """Raises NotFoundError for an unknown review."""
        ...

    async def list_reviews(
        self,
        app_id: str,
        *,
        max_rating: int | None = None,
        since: datetime | None = None,
    ) -> list[CustomerReview]:
        """Newest first. `max_rating` keeps reviews rated at or below it (e.g. 2 = complaints)."""
        ...

    async def respond_to_review(self, review_id: str, body: str) -> ReviewResponse:
        """Publicly visible. Raises NotFoundError, or AlreadyExistsError if already answered."""
        ...


@runtime_checkable
class AppStoreGateway(AppCatalog, BuildCatalog, TesterDirectory, ReviewInbox, Protocol):
    """Everything the application needs from App Store Connect in one object. Only the
    composition root asks for this; each service still depends on its single port."""
