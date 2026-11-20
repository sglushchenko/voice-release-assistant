"""Pending actions: inspect, confirm, reject.

Confirming is the sensitive part (ADR 0004). The body names the channel the confirmation
came through, and the credential must hold the matching `confirm:<channel>` scope, so a
client can only claim a channel it is trusted to speak for. An agent credential holds
neither and cannot confirm anything.
"""

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

from release_assistant.api.deps import ApplicationDep, PrincipalDep, ensure_scope
from release_assistant.api.routes.sessions import ActionOutcome
from release_assistant.application.pending import PendingAction, PendingStatus
from release_assistant.application.risk import ConfirmationChannel

router = APIRouter(prefix="/v1/actions", tags=["actions"])

_REJECT_SCOPES = ("propose", "confirm:button", "confirm:voice")


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: ConfirmationChannel


@router.get("")
async def list_actions(
    application: ApplicationDep,
    principal: PrincipalDep,
    status: Annotated[PendingStatus | None, Query()] = None,
) -> list[PendingAction]:
    """The caller's account's actions, newest first. Any authenticated client may look:
    results are limited to its own account."""
    return await application.actions.list_actions(owner=principal.owner, status=status)


@router.get("/{action_id}")
async def get_action(
    action_id: str, application: ApplicationDep, principal: PrincipalDep
) -> PendingAction:
    return await application.actions.get(action_id, owner=principal.owner)


@router.post("/{action_id}/confirm")
async def confirm_action(
    action_id: str, body: ConfirmRequest, application: ApplicationDep, principal: PrincipalDep
) -> ActionOutcome:
    ensure_scope(principal, f"confirm:{body.channel.value}")
    executed = await application.actions.confirm(action_id, body.channel, owner=principal.owner)
    return ActionOutcome(status="executed", action=executed.action, result=executed.result)


@router.post("/{action_id}/reject")
async def reject_action(
    action_id: str, application: ApplicationDep, principal: PrincipalDep
) -> PendingAction:
    """Cancelling is harmless, so the proposing client may do it as well as a confirming one."""
    ensure_scope(principal, *_REJECT_SCOPES)
    return await application.actions.reject(action_id, owner=principal.owner)
