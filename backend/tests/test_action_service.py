from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel, ValidationError

from release_assistant.application.actions import ActionDefinition
from release_assistant.application.errors import (
    ActionExpiredError,
    ActionNotPendingError,
    ChannelNotAllowedError,
)
from release_assistant.application.pending import PendingAction, PendingStatus
from release_assistant.application.policy import ConfirmationContext, ConfirmationPolicy
from release_assistant.application.risk import (
    ActionSpec,
    ApprovalMode,
    ConfirmationChannel,
    RiskTier,
)
from release_assistant.application.service import ActionService, Executed
from release_assistant.domain.errors import NotFoundError
from release_assistant.infrastructure.pending_store import InMemoryPendingActionStore

OWNER = "alice"


class Args(BaseModel):
    target: str


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


class Harness:
    """Service wired with one fake action that records every execution."""

    def __init__(self, mode: ApprovalMode, tier: RiskTier = RiskTier.REVERSIBLE_WRITE) -> None:
        self.calls: list[str] = []
        self.fail = False
        self.clock = Clock()
        self.store = InMemoryPendingActionStore()

        async def handler(args: Args) -> str:
            if self.fail:
                raise RuntimeError("boom")
            self.calls.append(args.target)
            return "ok"

        async def describe(args: Args) -> str:
            return f"Touch {args.target}"

        definition = ActionDefinition(
            spec=ActionSpec("touch", tier),
            arguments_model=Args,
            describe=describe,
            handler=handler,
        )
        self.service = ActionService(
            definitions=[definition],
            policy=ConfirmationPolicy({"touch": mode}),
            store=self.store,
            ttl=timedelta(minutes=5),
            clock=self.clock,
        )

    async def propose_pending(self, context: ConfirmationContext | None = None) -> PendingAction:
        outcome = await self.service.propose("touch", {"target": "x"}, owner=OWNER, context=context)
        assert isinstance(outcome, PendingAction)
        return outcome


async def test_propose_does_not_execute_and_describes_validated_arguments() -> None:
    h = Harness(ApprovalMode.BUTTON_ONLY)

    pending = await h.propose_pending()

    assert h.calls == []
    assert pending.status is PendingStatus.PENDING
    assert pending.summary == "Touch x"


async def test_button_confirmation_executes_exactly_once() -> None:
    h = Harness(ApprovalMode.BUTTON_ONLY)
    pending = await h.propose_pending()

    executed = await h.service.confirm(pending.id, ConfirmationChannel.BUTTON, owner=OWNER)

    assert isinstance(executed, Executed)
    assert executed.result == "ok"
    assert executed.action.confirmed_via is ConfirmationChannel.BUTTON
    assert h.calls == ["x"]
    with pytest.raises(ActionNotPendingError):
        await h.service.confirm(pending.id, ConfirmationChannel.BUTTON, owner=OWNER)
    assert h.calls == ["x"]


async def test_voice_cannot_confirm_button_only_action() -> None:
    h = Harness(ApprovalMode.BUTTON_ONLY)
    pending = await h.propose_pending()

    with pytest.raises(ChannelNotAllowedError):
        await h.service.confirm(pending.id, ConfirmationChannel.VOICE, owner=OWNER)

    assert h.calls == []
    stored = await h.store.get(pending.id)
    assert stored is not None
    assert stored.status is PendingStatus.PENDING


async def test_voice_confirms_when_mode_allows_it() -> None:
    h = Harness(ApprovalMode.VOICE_OR_BUTTON)
    pending = await h.propose_pending()

    await h.service.confirm(pending.id, ConfirmationChannel.VOICE, owner=OWNER)

    assert h.calls == ["x"]


async def test_implicit_mode_executes_immediately() -> None:
    h = Harness(ApprovalMode.IMPLICIT_IF_CLEAR)

    outcome = await h.service.propose("touch", {"target": "x"}, owner=OWNER)

    assert isinstance(outcome, Executed)
    assert outcome.action.confirmed_via is None
    assert h.calls == ["x"]


async def test_untrusted_content_turns_implicit_into_pending_button_only() -> None:
    h = Harness(ApprovalMode.IMPLICIT_IF_CLEAR)

    pending = await h.propose_pending(ConfirmationContext(untrusted_content_seen=True))

    assert pending.mode is ApprovalMode.BUTTON_ONLY
    assert h.calls == []


async def test_expired_action_cannot_be_confirmed() -> None:
    h = Harness(ApprovalMode.BUTTON_ONLY)
    pending = await h.propose_pending()
    h.clock.now += timedelta(minutes=6)

    with pytest.raises(ActionExpiredError):
        await h.service.confirm(pending.id, ConfirmationChannel.BUTTON, owner=OWNER)

    stored = await h.store.get(pending.id)
    assert stored is not None
    assert stored.status is PendingStatus.EXPIRED
    assert h.calls == []


async def test_rejected_action_cannot_be_confirmed() -> None:
    h = Harness(ApprovalMode.BUTTON_ONLY)
    pending = await h.propose_pending()

    await h.service.reject(pending.id, owner=OWNER)

    with pytest.raises(ActionNotPendingError):
        await h.service.confirm(pending.id, ConfirmationChannel.BUTTON, owner=OWNER)
    assert h.calls == []


async def test_handler_failure_marks_action_failed_and_is_not_retried() -> None:
    h = Harness(ApprovalMode.BUTTON_ONLY)
    pending = await h.propose_pending()
    h.fail = True

    with pytest.raises(RuntimeError):
        await h.service.confirm(pending.id, ConfirmationChannel.BUTTON, owner=OWNER)

    stored = await h.store.get(pending.id)
    assert stored is not None
    assert stored.status is PendingStatus.FAILED
    with pytest.raises(ActionNotPendingError):
        await h.service.confirm(pending.id, ConfirmationChannel.BUTTON, owner=OWNER)


async def test_invalid_arguments_store_nothing() -> None:
    h = Harness(ApprovalMode.BUTTON_ONLY)

    with pytest.raises(ValidationError):
        await h.service.propose("touch", {"wrong": 1}, owner=OWNER)


async def test_another_owner_cannot_see_confirm_or_reject_an_action() -> None:
    h = Harness(ApprovalMode.BUTTON_ONLY)
    pending = await h.propose_pending()

    with pytest.raises(NotFoundError):
        await h.service.get(pending.id, owner="mallory")
    with pytest.raises(NotFoundError):
        await h.service.confirm(pending.id, ConfirmationChannel.BUTTON, owner="mallory")
    with pytest.raises(NotFoundError):
        await h.service.reject(pending.id, owner="mallory")

    assert h.calls == []
    assert [a.id for a in await h.service.list_actions(owner=OWNER)] == [pending.id]
    assert await h.service.list_actions(owner="mallory") == []


async def test_list_actions_filters_by_status_newest_first() -> None:
    h = Harness(ApprovalMode.BUTTON_ONLY)
    first = await h.propose_pending()
    h.clock.now += timedelta(seconds=1)
    second = await h.propose_pending()
    await h.service.reject(first.id, owner=OWNER)

    everything = await h.service.list_actions(owner=OWNER)
    still_pending = await h.service.list_actions(owner=OWNER, status=PendingStatus.PENDING)

    assert [a.id for a in everything] == [second.id, first.id]
    assert [a.id for a in still_pending] == [second.id]


async def test_unknown_action_and_unknown_id_raise_not_found() -> None:
    h = Harness(ApprovalMode.BUTTON_ONLY)

    with pytest.raises(NotFoundError):
        await h.service.propose("nope", {}, owner=OWNER)
    with pytest.raises(NotFoundError):
        await h.service.confirm("missing", ConfirmationChannel.BUTTON, owner=OWNER)


def test_read_actions_cannot_be_registered_for_confirmation() -> None:
    async def handler(_: Args) -> None: ...

    async def describe(args: Args) -> str:
        return args.target

    with pytest.raises(ValueError, match="read actions"):
        ActionDefinition(
            spec=ActionSpec("list", RiskTier.READ),
            arguments_model=Args,
            describe=describe,
            handler=handler,
        )
