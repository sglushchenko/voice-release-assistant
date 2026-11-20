"""Sessions, review reads inside a session, and proposing actions.

Reviews are third-party text. Reading them marks the session untrusted (ADR 0003), which is
why they are only reachable through a session, and why the payload says so explicitly.
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from release_assistant.api.auth import Principal
from release_assistant.api.deps import ApplicationDep, require_scope
from release_assistant.application.pending import PendingAction
from release_assistant.application.service import Executed
from release_assistant.domain.entities import CustomerReview

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])

ReadPrincipal = Annotated[Principal, Depends(require_scope("read"))]
ProposePrincipal = Annotated[Principal, Depends(require_scope("propose"))]


class SessionCreated(BaseModel):
    session_id: str


class ReviewsResponse(BaseModel):
    """`trust` is always "untrusted": whoever feeds this to a model must treat the text as
    data, never as instructions."""

    source: Literal["customer_reviews"] = "customer_reviews"
    trust: Literal["untrusted"] = "untrusted"
    reviews: list[CustomerReview]


class ProposeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1, max_length=64)
    arguments: dict[str, Any]


class ActionOutcome(BaseModel):
    """`pending` means a person still has to confirm; `executed` means it already ran
    (only for actions whose approval mode allows that) and `result` holds the outcome."""

    status: Literal["pending", "executed"]
    action: PendingAction
    result: Any = None


@router.post("", status_code=201)
async def create_session(application: ApplicationDep, principal: ReadPrincipal) -> SessionCreated:
    session_id, _ = application.sessions.create(principal.owner)
    return SessionCreated(session_id=session_id)


@router.get("/{session_id}/apps/{app_id}/reviews")
async def list_reviews(
    session_id: str,
    app_id: str,
    application: ApplicationDep,
    principal: ReadPrincipal,
    max_rating: Annotated[int | None, Query(ge=1, le=5)] = None,
    since: datetime | None = None,
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
) -> ReviewsResponse:
    session = application.sessions.get(session_id, principal.owner)
    reviews = await session.reviews.list_reviews(
        app_id, max_rating=max_rating, since=since, limit=limit
    )
    return ReviewsResponse(reviews=reviews)


@router.get("/{session_id}/apps/{app_id}/reviews/unanswered")
async def unanswered_reviews(
    session_id: str,
    app_id: str,
    application: ApplicationDep,
    principal: ReadPrincipal,
    max_rating: Annotated[int | None, Query(ge=1, le=5)] = None,
) -> ReviewsResponse:
    session = application.sessions.get(session_id, principal.owner)
    return ReviewsResponse(reviews=await session.reviews.unanswered(app_id, max_rating=max_rating))


@router.post("/{session_id}/actions", status_code=200)
async def propose_action(
    session_id: str,
    body: ProposeRequest,
    application: ApplicationDep,
    principal: ProposePrincipal,
    response: Response,
) -> ActionOutcome:
    """Propose a write. The reply is 202 with `pending` when a person must confirm."""
    session = application.sessions.get(session_id, principal.owner)
    outcome = await session.propose(body.action, body.arguments)
    if isinstance(outcome, Executed):
        return ActionOutcome(status="executed", action=outcome.action, result=outcome.result)
    response.status_code = 202
    return ActionOutcome(status="pending", action=outcome)
