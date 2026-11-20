import pytest

from release_assistant.application.errors import ChannelNotAllowedError
from release_assistant.application.pending import PendingAction
from release_assistant.application.risk import ApprovalMode, ConfirmationChannel
from release_assistant.bootstrap import build_application, build_mock_application
from release_assistant.domain import ports
from release_assistant.infrastructure.fixtures import sample_gateway


def test_mock_gateway_satisfies_the_composite_port() -> None:
    assert isinstance(sample_gateway(), ports.AppStoreGateway)


async def test_end_to_end_read_reply_confirm() -> None:
    app = build_mock_application()
    session = app.start_session()

    [found] = await app.apps.find_by_name("pocket notes")
    complaints = await session.reviews.list_reviews(found.id, max_rating=1)
    pending = await session.propose(
        "respond_to_review", {"review_id": complaints[0].id, "body": "Fixed in 1.4.1."}
    )
    assert isinstance(pending, PendingAction)

    await app.actions.confirm(pending.id, ConfirmationChannel.BUTTON)

    after = await app.start_session().reviews.list_reviews(found.id, max_rating=1)
    assert after[0].response is not None
    assert after[0].response.body == "Fixed in 1.4.1."


async def test_session_attaches_trust_automatically() -> None:
    app = build_mock_application()
    session = app.start_session()
    await session.reviews.list_reviews("app-1")

    pending = await session.propose(
        "add_tester", {"group_id": "group-1", "email": "new@example.com"}
    )

    assert isinstance(pending, PendingAction)
    assert pending.mode is ApprovalMode.BUTTON_ONLY
    with pytest.raises(ChannelNotAllowedError):
        await app.actions.confirm(pending.id, ConfirmationChannel.VOICE)


async def test_sessions_do_not_share_trust() -> None:
    app = build_mock_application()
    tainted = app.start_session()
    clean = app.start_session()
    await tainted.reviews.list_reviews("app-1")

    pending = await clean.propose("add_tester", {"group_id": "group-1", "email": "new@example.com"})

    assert isinstance(pending, PendingAction)
    assert pending.mode is ApprovalMode.VOICE_OR_BUTTON


async def test_overrides_tighten_but_cannot_loosen_below_the_floor() -> None:
    app = build_application(
        sample_gateway(),
        approval_modes={
            "add_tester": ApprovalMode.BUTTON_ONLY,
            "remove_tester": ApprovalMode.IMPLICIT_IF_CLEAR,
        },
    )
    session = app.start_session()

    add = await session.propose("add_tester", {"group_id": "group-1", "email": "x@example.com"})
    remove = await session.propose(
        "remove_tester", {"group_id": "group-2", "tester_id": "tester-b"}
    )

    assert isinstance(add, PendingAction)
    assert add.mode is ApprovalMode.BUTTON_ONLY
    assert isinstance(remove, PendingAction)
    assert remove.mode is ApprovalMode.BUTTON_ONLY
