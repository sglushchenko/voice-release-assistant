"""Login, logout and "who am I" (ADR 0005)."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field

from release_assistant.api.deps import AuthDep, PrincipalDep, bearer_scheme

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=128)
    # Bounded so an enormous "password" cannot be used to burn CPU in the hash function.
    password: str = Field(min_length=1, max_length=1024)


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105  # OAuth field name, not a secret
    expires_in: int


class Me(BaseModel):
    subject: str
    owner: str
    scopes: list[str]


@router.post("/login")
async def login(
    body: LoginRequest, request: Request, response: Response, auth: AuthDep
) -> TokenResponse:
    """Exchange the admin username and password for a bearer token (8 hours by default,
    extended by each use). Repeated failures lock the client address out."""
    client = request.client.host if request.client else "unknown"
    result = await auth.login(body.username, body.password, client)
    response.headers["Cache-Control"] = "no-store"
    return TokenResponse(access_token=result.access_token, expires_in=result.expires_in)


@router.post("/logout", status_code=204)
async def logout(
    auth: AuthDep,
    _: PrincipalDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> Response:
    """Revoke the token used for this request. Static API keys are unaffected."""
    if credentials is not None:
        auth.logout(credentials.credentials)
    return Response(status_code=204)


@router.get("/me")
async def me(principal: PrincipalDep) -> Me:
    return Me(subject=principal.subject, owner=principal.owner, scopes=sorted(principal.scopes))
