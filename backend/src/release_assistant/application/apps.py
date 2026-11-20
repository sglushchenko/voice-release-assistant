"""App queries: finding apps by the name a person says, and where their versions stand.

Read-only, so nothing here goes through confirmation.
"""

from collections.abc import Collection
from dataclasses import dataclass

from release_assistant.domain.entities import App, AppStoreVersion, AppStoreVersionState
from release_assistant.domain.ports import AppCatalog

_IN_REVIEW = frozenset({AppStoreVersionState.WAITING_FOR_REVIEW, AppStoreVersionState.IN_REVIEW})
_REJECTED = frozenset({AppStoreVersionState.REJECTED})


@dataclass(frozen=True)
class AppVersion:
    """A version together with its app, so a cross-app answer needs no second lookup."""

    app: App
    version: AppStoreVersion


class AppsService:
    """
    Args:
        catalog: Source of apps and versions; mock or real.
    """

    def __init__(self, catalog: AppCatalog) -> None:
        self._catalog = catalog

    async def find_by_name(self, query: str) -> list[App]:
        """Resolve a spoken or typed app name.

        Exact name matches (case-insensitive) win; otherwise names or bundle ids containing
        the query. More than one result means the name is ambiguous and the caller should
        ask the user instead of guessing.
        """
        needle = query.strip().casefold()
        if not needle:
            return []
        apps = await self._catalog.list_apps()
        exact = [a for a in apps if a.name.casefold() == needle]
        if exact:
            return exact
        return [a for a in apps if needle in a.name.casefold() or needle in a.bundle_id.casefold()]

    async def list_versions(
        self, app_id: str, *, states: Collection[AppStoreVersionState] | None = None
    ) -> list[AppStoreVersion]:
        """Store versions of one app, optionally limited to the given states."""
        versions = await self._catalog.list_versions(app_id)
        if states is None:
            return versions
        return [v for v in versions if v.state in states]

    async def in_review(self) -> list[AppVersion]:
        """Versions waiting for or undergoing App Review, across all apps."""
        return await self._across_apps(_IN_REVIEW)

    async def rejected(self) -> list[AppVersion]:
        """Versions App Review rejected; they need a developer response."""
        return await self._across_apps(_REJECTED)

    async def _across_apps(self, states: Collection[AppStoreVersionState]) -> list[AppVersion]:
        result: list[AppVersion] = []
        for app in await self._catalog.list_apps():
            versions = await self.list_versions(app.id, states=states)
            result.extend(AppVersion(app, v) for v in versions)
        return result
