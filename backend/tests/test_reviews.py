from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from release_assistant.application.errors import ChannelNotAllowedError
from release_assistant.application.pending import PendingAction
from release_assistant.application.policy import ConfirmationPolicy
from release_assistant.application.reviews import (
    DEFAULT_APPROVAL_MODES,
    RESPOND_TO_REVIEW,
    UNTRUSTED_SOURCE,
    ReviewsService,
    build_review_actions,
)
from release_assistant.application.risk import ConfirmationChannel
from release_assistant.application.service import ActionService
from release_assistant.application.trust import TrustTracker
from release_assistant.domain.entities import ReviewResponseState
from release_assistant.domain.errors import AlreadyExistsError, NotFoundError
from release_assistant.infrastructure.fixtures import sample_gateway
from release_assistant.infrastructure.mock_gateway import MockAppStoreGateway
from release_assistant.infrastructure.pending_store import InMemoryPendingActionStore

OWNER = "alice"


def build() -> tuple[ReviewsService, ActionService, TrustTracker, MockAppStoreGateway]:
    gateway = sample_gateway()
    trust = TrustTracker()
    reviews = ReviewsService(gateway, trust)
    actions = ActionService(
        definitions=build_review_actions(gateway),
        policy=ConfirmationPolicy(DEFAULT_APPROVAL_MODES),
        store=InMemoryPendingActionStore(),
    )
    return reviews, actions, trust, gateway


async def test_reading_reviews_marks_the_session_untrusted() -> None:
    reviews, _, trust, _ = build()

    found = await reviews.list_reviews("app-1")

    assert len(found) == 3
    assert trust.untrusted_sources == (UNTRUSTED_SOURCE,)


async def test_an_empty_result_does_not_mark_the_session() -> None:
    reviews, _, trust, _ = build()

    assert (
        await reviews.list_reviews("app-1", max_rating=1, since=datetime(2027, 1, 1, tzinfo=UTC))
        == []
    )

    assert trust.context().untrusted_content_seen is False


async def test_filters_and_limit() -> None:
    reviews, _, _, _ = build()

    complaints = await reviews.list_reviews("app-1", max_rating=2)
    newest = await reviews.list_reviews("app-1", limit=1)

    assert [r.id for r in complaints] == ["review-2", "review-3"]
    assert [r.id for r in newest] == ["review-1"]


@pytest.mark.parametrize("limit", [0, -3])
async def test_non_positive_limit_is_rejected(limit: int) -> None:
    reviews, _, _, _ = build()

    with pytest.raises(ValueError, match="limit"):
        await reviews.list_reviews("app-1", limit=limit)


async def test_unanswered_excludes_reviews_with_a_response() -> None:
    reviews, _, _, gateway = build()
    await gateway.respond_to_review("review-2", "Sorry!")

    unanswered = await reviews.unanswered("app-1")

    assert [r.id for r in unanswered] == ["review-1", "review-3"]


async def test_unknown_app_propagates_not_found() -> None:
    reviews, _, _, _ = build()

    with pytest.raises(NotFoundError):
        await reviews.list_reviews("missing")


async def test_reply_summary_quotes_the_exact_text_to_be_published() -> None:
    _, actions, _, _ = build()

    pending = await actions.propose(
        RESPOND_TO_REVIEW, {"review_id": "review-2", "body": "Fixed in 1.4.1."}, owner=OWNER
    )

    assert isinstance(pending, PendingAction)
    assert pending.summary == (
        "Publish a public reply to the 1-star review 'Crashes on launch': \"Fixed in 1.4.1.\""
    )


async def test_reply_can_be_confirmed_by_voice_when_no_untrusted_text_was_read() -> None:
    _, actions, trust, gateway = build()
    pending = await actions.propose(
        RESPOND_TO_REVIEW,
        {"review_id": "review-2", "body": "Thanks!"},
        owner=OWNER,
        context=trust.context(),
    )
    assert isinstance(pending, PendingAction)

    await actions.confirm(pending.id, ConfirmationChannel.VOICE, owner=OWNER)

    review = await gateway.get_review("review-2")
    assert review.response is not None
    assert review.response.state is ReviewResponseState.PENDING_PUBLISH


async def test_after_reading_reviews_a_reply_needs_the_button() -> None:
    reviews, actions, trust, gateway = build()
    await reviews.list_reviews("app-1")
    pending = await actions.propose(
        RESPOND_TO_REVIEW,
        {"review_id": "review-2", "body": "Thanks!"},
        owner=OWNER,
        context=trust.context(),
    )
    assert isinstance(pending, PendingAction)

    with pytest.raises(ChannelNotAllowedError):
        await actions.confirm(pending.id, ConfirmationChannel.VOICE, owner=OWNER)
    assert (await gateway.get_review("review-2")).response is None

    await actions.confirm(pending.id, ConfirmationChannel.BUTTON, owner=OWNER)

    assert (await gateway.get_review("review-2")).response is not None


async def test_replying_to_an_answered_or_unknown_review_fails_at_proposal() -> None:
    _, actions, _, gateway = build()
    await gateway.respond_to_review("review-2", "Already answered")

    with pytest.raises(AlreadyExistsError):
        await actions.propose(
            RESPOND_TO_REVIEW, {"review_id": "review-2", "body": "Again"}, owner=OWNER
        )
    with pytest.raises(NotFoundError):
        await actions.propose(RESPOND_TO_REVIEW, {"review_id": "nope", "body": "Hi"}, owner=OWNER)


@pytest.mark.parametrize(
    "args",
    [
        {"review_id": "review-1", "body": "   "},
        {"review_id": "review-1", "body": "x" * 5001},
        {"review_id": "review-1", "body": "Hi", "rating": 5},
    ],
)
async def test_invalid_reply_arguments_are_rejected(args: dict[str, object]) -> None:
    _, actions, _, _ = build()

    with pytest.raises(ValidationError):
        await actions.propose(RESPOND_TO_REVIEW, args, owner=OWNER)
