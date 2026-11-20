"""Runs risky actions through propose -> confirm, enforcing the policy (ADR 0002).

This is the only code path that executes a write. Whoever drives it (a model, an HTTP
endpoint, a test) can only propose; approval has to arrive through `confirm` on an
allowed channel.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import structlog

from release_assistant.application.actions import ActionDefinition
from release_assistant.application.errors import (
    ActionExpiredError,
    ActionNotPendingError,
    ChannelNotAllowedError,
)
from release_assistant.application.pending import (
    PendingAction,
    PendingActionStore,
    PendingStatus,
)
from release_assistant.application.policy import ConfirmationContext, ConfirmationPolicy
from release_assistant.application.risk import ApprovalMode, ConfirmationChannel
from release_assistant.domain.errors import NotFoundError

log = structlog.get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Executed:
    """Outcome of an action that ran, either implicitly or after confirmation."""

    action: PendingAction
    result: Any


class ActionService:
    """
    Args:
        definitions: Every action this service may run.
        policy: Resolves the approval mode per action.
        store: Holds pending actions between propose and confirm.
        ttl: How long a proposal stays confirmable.
        clock: Injected so tests control expiry.
    """

    def __init__(
        self,
        *,
        definitions: Iterable[ActionDefinition],
        policy: ConfirmationPolicy,
        store: PendingActionStore,
        ttl: timedelta = timedelta(minutes=5),
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._definitions = {d.spec.name: d for d in definitions}
        self._policy = policy
        self._store = store
        self._ttl = ttl
        self._clock = clock

    async def propose(
        self,
        name: str,
        arguments: dict[str, Any],
        context: ConfirmationContext | None = None,
    ) -> PendingAction | Executed:
        """Validate and register an action.

        Returns a PendingAction to show the user, or Executed when the resolved mode is
        IMPLICIT_IF_CLEAR. Raises pydantic.ValidationError for bad arguments; nothing is
        stored in that case.
        """
        definition = self._definition(name)
        args = definition.arguments_model.model_validate(arguments)
        mode = self._policy.effective_mode(definition.spec, context)
        now = self._clock()
        pending = PendingAction(
            id=uuid4().hex,
            action=name,
            tier=definition.spec.tier,
            summary=definition.describe(args),
            arguments=args.model_dump(mode="json"),
            mode=mode,
            created_at=now,
            expires_at=now + self._ttl,
        )
        await self._store.add(pending)
        log.info("action.proposed", action_id=pending.id, action=name, mode=mode.name)
        if mode is ApprovalMode.IMPLICIT_IF_CLEAR:
            return await self._execute(definition, pending, channel=None)
        return pending

    async def confirm(self, action_id: str, channel: ConfirmationChannel) -> Executed:
        """Approve and run a pending action. `channel` must be set by the client or transport,
        never taken from model output."""
        pending = await self._store.get(action_id)
        if pending is None:
            raise NotFoundError(f"Action {action_id!r} not found")
        if pending.status is not PendingStatus.PENDING:
            raise ActionNotPendingError(f"Action {action_id!r} is {pending.status.value}")
        if self._clock() >= pending.expires_at:
            await self._store.transition(
                action_id, expected=PendingStatus.PENDING, new=PendingStatus.EXPIRED
            )
            log.info("action.expired", action_id=action_id)
            raise ActionExpiredError(f"Action {action_id!r} expired")
        if not self._policy.accepts(pending.mode, channel):
            log.warning(
                "action.channel_denied",
                action_id=action_id,
                mode=pending.mode.name,
                channel=channel.value,
            )
            raise ChannelNotAllowedError(
                f"{pending.mode.name} does not accept confirmation via {channel.value}"
            )
        return await self._execute(self._definition(pending.action), pending, channel)

    async def reject(self, action_id: str) -> PendingAction:
        """Cancel a pending action without running it."""
        rejected = await self._store.transition(
            action_id, expected=PendingStatus.PENDING, new=PendingStatus.REJECTED
        )
        if rejected is None:
            raise ActionNotPendingError(f"Action {action_id!r} is not pending")
        log.info("action.rejected", action_id=action_id)
        return rejected

    def _definition(self, name: str) -> ActionDefinition:
        try:
            return self._definitions[name]
        except KeyError:
            raise NotFoundError(f"Unknown action {name!r}") from None

    async def _execute(
        self,
        definition: ActionDefinition,
        pending: PendingAction,
        channel: ConfirmationChannel | None,
    ) -> Executed:
        # Claiming first guarantees at most one execution, even with concurrent confirms.
        claimed = await self._store.transition(
            pending.id,
            expected=PendingStatus.PENDING,
            new=PendingStatus.CONFIRMED,
            confirmed_via=channel,
        )
        if claimed is None:
            raise ActionNotPendingError(f"Action {pending.id!r} is no longer pending")
        args = definition.arguments_model.model_validate(claimed.arguments)
        try:
            result = await definition.handler(args)
        except Exception:
            await self._store.transition(
                pending.id, expected=PendingStatus.CONFIRMED, new=PendingStatus.FAILED
            )
            log.exception("action.failed", action_id=pending.id, action=pending.action)
            raise
        log.info(
            "action.executed",
            action_id=pending.id,
            action=pending.action,
            via=channel.value if channel else "implicit",
        )
        return Executed(action=claimed, result=result)
