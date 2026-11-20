"""Runs risky actions through propose -> confirm, enforcing the policy (ADR 0002).

This is the only code path that executes a write. Whoever drives it (a model, an HTTP
endpoint, a test) can only propose; approval has to arrive through `confirm` on an
allowed channel.
"""

from collections.abc import Callable, Collection, Iterable
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
from release_assistant.domain.errors import NotFoundError, NotSupportedError

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
        disabled_actions: Actions this backend cannot perform yet; proposing one is refused
            with NotSupportedError.
        ttl: How long a proposal stays confirmable.
        clock: Injected so tests control expiry.
    """

    def __init__(
        self,
        *,
        definitions: Iterable[ActionDefinition],
        policy: ConfirmationPolicy,
        store: PendingActionStore,
        disabled_actions: Collection[str] = (),
        ttl: timedelta = timedelta(minutes=5),
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._definitions = {d.spec.name: d for d in definitions}
        self._disabled = frozenset(disabled_actions)
        self._policy = policy
        self._store = store
        self._ttl = ttl
        self._clock = clock

    async def propose(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        owner: str,
        context: ConfirmationContext | None = None,
    ) -> PendingAction | Executed:
        """Validate and register an action on behalf of `owner`.

        `owner` is the account the action belongs to (not a credential): only that account
        can later confirm or reject it, whichever client it uses.

        Returns a PendingAction to show the user, or Executed when the resolved mode is
        IMPLICIT_IF_CLEAR. Raises pydantic.ValidationError for bad arguments; nothing is
        stored in that case.
        """
        if name in self._disabled:
            # Refused up front, so nobody is shown a confirmation that could only fail.
            raise NotSupportedError(f"'{name}' is not supported by this backend yet")
        definition = self._definition(name)
        args = definition.arguments_model.model_validate(arguments)
        mode = self._policy.effective_mode(definition.spec, context)
        now = self._clock()
        pending = PendingAction(
            id=uuid4().hex,
            owner=owner,
            action=name,
            tier=definition.spec.tier,
            summary=await definition.describe(args),
            arguments=args.model_dump(mode="json"),
            mode=mode,
            created_at=now,
            expires_at=now + self._ttl,
        )
        await self._store.add(pending)
        log.info("action.proposed", action_id=pending.id, action=name, mode=mode.value)
        if mode is ApprovalMode.IMPLICIT_IF_CLEAR:
            return await self._execute(definition, pending, channel=None)
        return pending

    async def get(self, action_id: str, *, owner: str) -> PendingAction:
        """One of `owner`'s actions. Someone else's action is reported as not found, so ids
        cannot be probed."""
        pending = await self._store.get(action_id)
        if pending is None or pending.owner != owner:
            raise NotFoundError(f"Action {action_id!r} not found")
        return pending

    async def list_actions(
        self, *, owner: str, status: PendingStatus | None = None
    ) -> list[PendingAction]:
        """`owner`'s actions, newest first, optionally limited to one status."""
        return await self._store.list_for_owner(owner, status=status)

    async def confirm(
        self, action_id: str, channel: ConfirmationChannel, *, owner: str
    ) -> Executed:
        """Approve and run a pending action. `channel` must be set by the client or transport,
        never taken from model output."""
        pending = await self.get(action_id, owner=owner)
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
                mode=pending.mode.value,
                channel=channel.value,
            )
            raise ChannelNotAllowedError(
                f"{pending.mode.value} does not accept confirmation via {channel.value}"
            )
        return await self._execute(self._definition(pending.action), pending, channel)

    async def reject(self, action_id: str, *, owner: str) -> PendingAction:
        """Cancel a pending action without running it."""
        await self.get(action_id, owner=owner)
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
