"""A proposed-but-not-yet-executed action, and the store that holds it."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from release_assistant.application.risk import ApprovalMode, ConfirmationChannel, RiskTier
from release_assistant.domain.entities import Entity


class PendingStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    FAILED = "failed"


class PendingAction(Entity):
    """Also serves as the audit record. `summary` is generated from validated arguments by
    code, never written by a model, so a client can show it to the user as is.

    `mode` is the approval mode resolved when the action was proposed and stays fixed for
    its lifetime.
    """

    id: str
    owner: str
    action: str
    tier: RiskTier
    summary: str
    arguments: dict[str, Any]
    mode: ApprovalMode
    created_at: datetime
    expires_at: datetime
    status: PendingStatus = PendingStatus.PENDING
    confirmed_via: ConfirmationChannel | None = None


class PendingActionStore(Protocol):
    """Persistence for pending actions. Memory now; a database when the service scales out."""

    async def add(self, action: PendingAction) -> None: ...

    async def get(self, action_id: str) -> PendingAction | None: ...

    async def list_for_owner(
        self, owner: str, *, status: PendingStatus | None = None
    ) -> list[PendingAction]:
        """Newest first."""
        ...

    async def transition(
        self,
        action_id: str,
        *,
        expected: PendingStatus,
        new: PendingStatus,
        confirmed_via: ConfirmationChannel | None = None,
    ) -> PendingAction | None:
        """Atomic compare-and-set. Returns None if the action is missing or not in `expected`,
        which is how two simultaneous confirmations are kept from both executing."""
        ...
