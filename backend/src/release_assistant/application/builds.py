"""Build queries: the questions a release manager actually asks about builds.

Read-only, so nothing here goes through confirmation. Kept as its own service so build
logic grows independently of apps, versions and testers.
"""

from collections.abc import Collection

from release_assistant.domain.entities import Build, BuildProcessingState
from release_assistant.domain.ports import BuildCatalog

_PROBLEM_STATES = frozenset({BuildProcessingState.FAILED, BuildProcessingState.INVALID})


class BuildsService:
    """
    Args:
        catalog: Source of builds; mock or real, the service does not care.
    """

    def __init__(self, catalog: BuildCatalog) -> None:
        self._catalog = catalog

    async def list_builds(
        self,
        app_id: str,
        *,
        states: Collection[BuildProcessingState] | None = None,
        limit: int | None = None,
    ) -> list[Build]:
        """Builds newest first.

        Args:
            app_id: App to look at.
            states: Keep only builds in these processing states.
            limit: Maximum number to return; must be positive.
        """
        if limit is not None and limit < 1:
            raise ValueError("limit must be positive")
        builds = await self._catalog.list_builds(app_id)
        if states is not None:
            builds = [b for b in builds if b.processing_state in states]
        return builds[:limit]

    async def latest(self, app_id: str) -> Build | None:
        """The most recently uploaded build, or None if the app has none."""
        builds = await self.list_builds(app_id, limit=1)
        return builds[0] if builds else None

    async def problem_builds(self, app_id: str) -> list[Build]:
        """Builds that failed processing or were rejected as invalid; they need attention."""
        return await self.list_builds(app_id, states=_PROBLEM_STATES)
