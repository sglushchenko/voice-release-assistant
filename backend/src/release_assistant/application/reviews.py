"""Customer reviews: read queries and the public reply action.

Review text is written by third parties, so every read marks the session as having seen
untrusted content (see `TrustTracker`). Publishing a reply is a public write.
"""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from release_assistant.application.actions import ActionDefinition
from release_assistant.application.risk import ActionSpec, ApprovalMode, RiskTier
from release_assistant.application.trust import TrustTracker
from release_assistant.domain.entities import CustomerReview, ReviewResponse
from release_assistant.domain.errors import AlreadyExistsError
from release_assistant.domain.ports import ReviewInbox

RESPOND_TO_REVIEW = "respond_to_review"

DEFAULT_APPROVAL_MODES = {RESPOND_TO_REVIEW: ApprovalMode.VOICE_OR_BUTTON}

UNTRUSTED_SOURCE = "customer_reviews"

_TITLE_PREVIEW = 60
_MAX_REPLY_LENGTH = 5000


class ReviewsService:
    """
    Args:
        inbox: Source of reviews; mock or real.
        trust: The session's tracker; reads mark it so risky actions get stricter approval.
    """

    def __init__(self, inbox: ReviewInbox, trust: TrustTracker) -> None:
        self._inbox = inbox
        self._trust = trust

    async def list_reviews(
        self,
        app_id: str,
        *,
        max_rating: int | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[CustomerReview]:
        """Reviews newest first.

        Args:
            app_id: App to look at.
            max_rating: Keep reviews rated at or below this (2 = complaints).
            since: Keep reviews created at or after this moment.
            limit: Maximum number to return; must be positive.
        """
        if limit is not None and limit < 1:
            raise ValueError("limit must be positive")
        reviews = await self._inbox.list_reviews(app_id, max_rating=max_rating, since=since)
        return self._deliver(reviews[:limit])

    async def unanswered(
        self, app_id: str, *, max_rating: int | None = None
    ) -> list[CustomerReview]:
        """Reviews with no developer response yet, newest first."""
        reviews = await self._inbox.list_reviews(app_id, max_rating=max_rating)
        return self._deliver([r for r in reviews if r.response is None])

    def _deliver(self, reviews: list[CustomerReview]) -> list[CustomerReview]:
        if reviews:
            self._trust.mark_untrusted(UNTRUSTED_SOURCE)
        return reviews


class RespondToReviewArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    body: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=_MAX_REPLY_LENGTH)
    ]


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_review_actions(inbox: ReviewInbox) -> list[ActionDefinition]:
    """Build the review write actions over `inbox`.

    The description checks the review exists and is unanswered, so a doomed reply fails at
    proposal time instead of after the user approved it. It quotes the exact text that
    will be published.
    """

    async def describe(args: RespondToReviewArgs) -> str:
        review = await inbox.get_review(args.review_id)
        if review.response is not None:
            raise AlreadyExistsError(f"Review {args.review_id!r} already has a response")
        title = _shorten(review.title, _TITLE_PREVIEW)
        return (
            f"Publish a public reply to the {review.rating}-star review '{title}': \"{args.body}\""
        )

    async def respond(args: RespondToReviewArgs) -> ReviewResponse:
        return await inbox.respond_to_review(args.review_id, args.body)

    return [
        ActionDefinition(
            spec=ActionSpec(RESPOND_TO_REVIEW, RiskTier.PUBLIC_WRITE),
            arguments_model=RespondToReviewArgs,
            describe=describe,
            handler=respond,
        )
    ]
