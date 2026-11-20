"""TestFlight groups and testers (read-only; changes go through the action flow)."""

from fastapi import APIRouter, Depends

from release_assistant.api.deps import ApplicationDep, require_scope
from release_assistant.domain.entities import BetaGroup, BetaTester

router = APIRouter(prefix="/v1", dependencies=[Depends(require_scope("read"))], tags=["testers"])


@router.get("/apps/{app_id}/beta-groups")
async def list_groups(app_id: str, application: ApplicationDep) -> list[BetaGroup]:
    return await application.testers.list_groups(app_id)


@router.get("/beta-groups/{group_id}/testers")
async def list_testers(group_id: str, application: ApplicationDep) -> list[BetaTester]:
    return await application.testers.list_testers(group_id)
