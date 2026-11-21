"""FastAPI application factory."""

from typing import Any

import httpx
import structlog
from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.middleware.cors import CORSMiddleware

from release_assistant.agent.errors import LLMError
from release_assistant.agent.openai_model import create_openai_model
from release_assistant.agent.ports import LLMClient
from release_assistant.agent.service import ChatService
from release_assistant.api.body_limit import BodyLimitMiddleware
from release_assistant.api.errors import Problem, install_error_handlers
from release_assistant.api.login import AuthService
from release_assistant.api.middleware import install_request_context
from release_assistant.api.ratelimit import RateLimits, install_address_rate_limit
from release_assistant.api.routes import actions, auth, catalog, chat, reports, sessions, testers
from release_assistant.api.security_headers import SecurityHeadersMiddleware
from release_assistant.bootstrap import Application, build_application_from_settings
from release_assistant.config import Settings

log = structlog.get_logger(__name__)

DESCRIPTION = """\
Voice release assistant backend for App Store Connect.

**Authentication.** Every `/v1` route needs `Authorization: Bearer <token>`: a login token
from `POST /v1/auth/login` (the human) or a static API key (an agent).

**Writes need confirmation.** Changing anything is a two-step flow: propose an action in a
session, then a person confirms it with `POST /v1/actions/{id}/confirm`. Agent credentials
cannot confirm.

**Errors.** Every error is a `Problem` body with a stable machine-readable `code`.
Besides the codes listed per route, `409` (already done or no longer pending), `410`
(confirmation expired), `413` (body too large) and `503` (data not published yet) can occur.
"""

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": Problem, "description": "Missing or invalid credentials"},
    403: {"model": Problem, "description": "The credential lacks the required scope"},
    404: {"model": Problem, "description": "Not found, or not yours"},
    422: {"model": Problem, "description": "Invalid request"},
    429: {"model": Problem, "description": "Rate limit exceeded; see Retry-After"},
}

# Auth routes are reached before there is a caller with scopes or resources to look up.
_AUTH_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    code: _ERROR_RESPONSES[code] for code in (401, 422, 429)
}


def _operation_id(route: APIRoute) -> str:
    """Stable, readable ids such as `catalog_list_apps`, which become method names in
    generated clients (FastAPI's default, `list_apps_v1_apps_get`, is not)."""
    return f"{route.tags[0]}_{route.name}" if route.tags else route.name


def create_app(
    settings: Settings,
    application: Application | None = None,
    *,
    auth_service: AuthService | None = None,
    chat_model: LLMClient | None = None,
) -> FastAPI:
    """Build the API.

    Args:
        settings: Credentials and backend choice.
        application: Prebuilt application, mainly for tests. Defaults to the one `settings`
            selects.
        auth_service: Prebuilt authentication, so tests can control the clock.
        chat_model: Language model for the chat endpoint, mainly for tests. Defaults to the
            one `settings` configures; without either, chat answers 503.
    """
    app = FastAPI(
        title="Voice Release Assistant",
        version="0.1.0",
        description=DESCRIPTION,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
        generate_unique_id_function=_operation_id,
    )
    if application is None:
        log.warning(
            "app.backend_selected",
            backend=settings.backend,
            note="sample data"
            if settings.backend == "mock"
            else "real App Store Connect, read-only",
        )
        application = build_application_from_settings(settings)
    app.state.application = application
    app.state.auth = auth_service or AuthService(settings)
    app.state.limits = RateLimits(settings)
    if not app.state.auth.login_enabled:
        log.warning("app.login_disabled", note="set admin username and password hash to enable")
    if not settings.rate_limit_enabled:
        log.warning("app.rate_limit_disabled")

    # The last one added is outermost. CORS is first so a browser's preflight is answered
    # before authentication or rate limits; then security headers, so even refusals carry
    # them; then request context (every response gets an id and an access line), the
    # per-address limit, and the body cap closest to the routes.
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_request_bytes)
    install_address_rate_limit(app)
    install_request_context(app)
    app.add_middleware(SecurityHeadersMiddleware)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
            expose_headers=["X-Request-ID", "Retry-After"],
            max_age=600,
        )
    install_error_handlers(app)

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        """Liveness probe; unauthenticated and free of side effects."""
        return {"status": "ok"}

    app.state.chat = _create_chat(app, settings, application, chat_model)
    app.include_router(auth.router, responses=_AUTH_ERROR_RESPONSES)
    for router in (
        catalog.router,
        testers.router,
        reports.router,
        sessions.router,
        actions.router,
        chat.router,
    ):
        app.include_router(router, responses=_ERROR_RESPONSES)
    return app


def _create_chat(
    app: FastAPI, settings: Settings, application: Application, model: LLMClient | None
) -> ChatService:
    """The chat service, whose agent calls this same app in process with its own credential, so
    every safeguard applies to it exactly as to an outside client (ADR 0007)."""
    if model is None:
        try:
            model = create_openai_model(settings)
        except LLMError:
            log.warning("app.chat_disabled", note="set RELEASE_ASSISTANT_OPENAI_API_KEY")

    def client_for(owner: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://agent.internal",
            headers={"Authorization": f"Bearer {app.state.auth.agent_credential(owner)}"},
            timeout=settings.openai_timeout_seconds * 2,
        )

    return ChatService(
        model,
        client_for,
        application.sessions.get,
        daily_token_budget=settings.chat_daily_token_budget,
    )
