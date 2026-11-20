"""Talk to the assistant: one message in, the answer and any changes awaiting approval out.

The agent behind this route is an API client like any other, with read and propose rights only.
A change it proposes comes back in `pending_actions`, described by the server, and happens only
when a person confirms it through `POST /v1/actions/{id}/confirm`. Show those, not the model's
words: a model can claim a change was made that was not.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from release_assistant.agent.messages import StopReason
from release_assistant.agent.service import ChatService
from release_assistant.api.deps import ApplicationDep, PrincipalDep, ensure_scope
from release_assistant.api.deps import limit as rate_limit
from release_assistant.application.pending import PendingAction

router = APIRouter(prefix="/v1/chat", tags=["chat"])


def get_chat(request: Request) -> ChatService:
    chat: ChatService = request.app.state.chat
    return chat


ChatDep = Annotated[ChatService, Depends(get_chat)]


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = Field(
        default=None,
        max_length=64,
        description="Omit to start a conversation; send the id from a reply to continue it.",
    )


class ChatUsage(BaseModel):
    input_tokens: int
    output_tokens: int


class ChatResponse(BaseModel):
    """`stop_reason` other than `answered` means the server stopped the run and `answer` is a
    fixed message. `pending_actions` are the server's record of what awaits approval."""

    conversation_id: str
    answer: str
    stop_reason: StopReason
    steps: int
    tool_calls: int
    usage: ChatUsage
    pending_actions: list[PendingAction]


@router.post("", dependencies=[Depends(rate_limit("chat"))])
async def send_message(
    body: ChatRequest, chat: ChatDep, application: ApplicationDep, principal: PrincipalDep
) -> ChatResponse:
    ensure_scope(principal, "chat")
    reply = await chat.send(principal.owner, body.message, body.conversation_id)
    run = reply.run
    pending = [
        await application.actions.get(action_id, owner=principal.owner)
        for action_id in run.pending_action_ids
    ]
    return ChatResponse(
        conversation_id=reply.conversation_id,
        answer=run.answer,
        stop_reason=run.stop_reason,
        steps=run.steps,
        tool_calls=run.tool_calls_made,
        usage=ChatUsage(input_tokens=run.usage.input_tokens, output_tokens=run.usage.output_tokens),
        pending_actions=pending,
    )
