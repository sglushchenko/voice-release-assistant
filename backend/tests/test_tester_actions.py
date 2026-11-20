import pytest
from pydantic import ValidationError

from release_assistant.application.errors import ChannelNotAllowedError
from release_assistant.application.pending import PendingAction
from release_assistant.application.policy import ConfirmationContext, ConfirmationPolicy
from release_assistant.application.risk import ConfirmationChannel
from release_assistant.application.service import ActionService
from release_assistant.application.testers import (
    ADD_TESTER,
    DEFAULT_APPROVAL_MODES,
    REMOVE_TESTER,
    build_tester_actions,
)
from release_assistant.domain.errors import NotFoundError
from release_assistant.infrastructure.fixtures import sample_gateway
from release_assistant.infrastructure.mock_gateway import MockAppStoreGateway
from release_assistant.infrastructure.pending_store import InMemoryPendingActionStore

OWNER = "alice"


def build() -> tuple[ActionService, MockAppStoreGateway]:
    gateway = sample_gateway()
    service = ActionService(
        definitions=build_tester_actions(gateway),
        policy=ConfirmationPolicy(DEFAULT_APPROVAL_MODES),
        store=InMemoryPendingActionStore(),
    )
    return service, gateway


async def propose(service: ActionService, name: str, args: dict[str, str]) -> PendingAction:
    outcome = await service.propose(name, args, owner=OWNER)
    assert isinstance(outcome, PendingAction)
    return outcome


async def test_add_tester_summary_names_the_group_and_normalized_email() -> None:
    service, _ = build()

    pending = await propose(
        service, ADD_TESTER, {"group_id": "group-1", "email": " New@Example.com "}
    )

    assert pending.summary == "Add tester new@example.com to TestFlight group 'Internal'"


async def test_add_tester_by_voice_changes_nothing_until_confirmed() -> None:
    service, gateway = build()
    pending = await propose(service, ADD_TESTER, {"group_id": "group-1", "email": "c@example.com"})

    assert len(await gateway.list_testers("group-1")) == 1

    await service.confirm(pending.id, ConfirmationChannel.VOICE, owner=OWNER)

    emails = [t.email for t in await gateway.list_testers("group-1")]
    assert emails == ["anna@example.com", "c@example.com"]


async def test_remove_tester_summary_shows_email_not_id() -> None:
    service, _ = build()

    pending = await propose(
        service, REMOVE_TESTER, {"group_id": "group-2", "tester_id": "tester-b"}
    )

    assert pending.summary == "Remove tester boris@example.com from TestFlight group 'Public Beta'"


async def test_remove_tester_requires_button() -> None:
    service, gateway = build()
    pending = await propose(
        service, REMOVE_TESTER, {"group_id": "group-2", "tester_id": "tester-b"}
    )

    with pytest.raises(ChannelNotAllowedError):
        await service.confirm(pending.id, ConfirmationChannel.VOICE, owner=OWNER)
    assert len(await gateway.list_testers("group-2")) == 2

    await service.confirm(pending.id, ConfirmationChannel.BUTTON, owner=OWNER)

    assert [t.id for t in await gateway.list_testers("group-2")] == ["tester-a"]


async def test_untrusted_content_makes_add_tester_button_only() -> None:
    service, _ = build()

    outcome = await service.propose(
        ADD_TESTER,
        {"group_id": "group-1", "email": "attacker@example.com"},
        owner=OWNER,
        context=ConfirmationContext(untrusted_content_seen=True),
    )
    assert isinstance(outcome, PendingAction)

    with pytest.raises(ChannelNotAllowedError):
        await service.confirm(outcome.id, ConfirmationChannel.VOICE, owner=OWNER)


@pytest.mark.parametrize(
    "args",
    [
        {"group_id": "group-1", "email": "not-an-email"},
        {"group_id": "group-1", "email": "a@example.com", "role": "admin"},
        {"group_id": "", "email": "a@example.com"},
    ],
)
async def test_invalid_arguments_are_rejected_before_anything_is_stored(
    args: dict[str, str],
) -> None:
    service, _ = build()

    with pytest.raises(ValidationError):
        await service.propose(ADD_TESTER, args, owner=OWNER)


async def test_unknown_group_or_tester_fails_at_proposal_time() -> None:
    service, _ = build()

    with pytest.raises(NotFoundError):
        await service.propose(
            ADD_TESTER, {"group_id": "nope", "email": "a@example.com"}, owner=OWNER
        )
    with pytest.raises(NotFoundError):
        await service.propose(
            REMOVE_TESTER, {"group_id": "group-1", "tester_id": "tester-b"}, owner=OWNER
        )
