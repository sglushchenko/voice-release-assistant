"""Apps, store versions and builds (read-only)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from release_assistant.api.deps import ApplicationDep, require_scope
from release_assistant.application.apps import AppVersion
from release_assistant.domain.entities import (
    App,
    AppStoreVersion,
    AppStoreVersionState,
    Build,
    BuildProcessingState,
)
from release_assistant.domain.errors import NotFoundError

router = APIRouter(prefix="/v1", dependencies=[Depends(require_scope("read"))], tags=["catalog"])


@router.get("/apps")
async def list_apps(
    application: ApplicationDep,
    name: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
) -> list[App]:
    """All apps, or those matching `name`. More than one match means the name is ambiguous."""
    if name is None:
        return await application.apps.list_apps()
    return await application.apps.find_by_name(name)


@router.get("/apps/{app_id}/versions")
async def list_versions(
    app_id: str, application: ApplicationDep, state: AppStoreVersionState | None = None
) -> list[AppStoreVersion]:
    states = None if state is None else {state}
    return await application.apps.list_versions(app_id, states=states)


@router.get("/versions/in-review")
async def versions_in_review(application: ApplicationDep) -> list[AppVersion]:
    """Versions waiting for or undergoing App Review, across all apps."""
    return await application.apps.in_review()


@router.get("/versions/rejected")
async def rejected_versions(application: ApplicationDep) -> list[AppVersion]:
    """Versions App Review rejected; they need a developer response."""
    return await application.apps.rejected()


@router.get("/apps/{app_id}/builds")
async def list_builds(
    app_id: str,
    application: ApplicationDep,
    state: BuildProcessingState | None = None,
    limit: Annotated[int | None, Query(ge=1, le=200)] = None,
) -> list[Build]:
    states = None if state is None else {state}
    return await application.builds.list_builds(app_id, states=states, limit=limit)


@router.get("/apps/{app_id}/builds/latest")
async def latest_build(app_id: str, application: ApplicationDep) -> Build:
    build = await application.builds.latest(app_id)
    if build is None:
        raise NotFoundError(f"App {app_id!r} has no builds")
    return build
