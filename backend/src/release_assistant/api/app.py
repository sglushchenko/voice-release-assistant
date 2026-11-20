"""FastAPI application factory."""

import structlog
from fastapi import FastAPI

from release_assistant.api.body_limit import BodyLimitMiddleware
from release_assistant.api.errors import install_error_handlers
from release_assistant.api.login import AuthService
from release_assistant.api.middleware import install_request_context
from release_assistant.api.ratelimit import RateLimits, install_address_rate_limit
from release_assistant.api.routes import actions, auth, catalog, reports, sessions, testers
from release_assistant.bootstrap import Application, build_mock_application
from release_assistant.config import Settings

log = structlog.get_logger(__name__)


def create_app(
    settings: Settings,
    application: Application | None = None,
    *,
    auth_service: AuthService | None = None,
) -> FastAPI:
    """Build the API.

    Args:
        settings: Credentials and backend choice.
        application: Prebuilt application, mainly for tests. Defaults to the one `settings`
            selects.
        auth_service: Prebuilt authentication, so tests can control the clock.
    """
    app = FastAPI(title="Voice Release Assistant", version="0.1.0")
    if application is None:
        log.warning("app.backend_selected", backend=settings.backend, note="mock data only")
        application = build_mock_application()
    app.state.application = application
    app.state.auth = auth_service or AuthService(settings)
    app.state.limits = RateLimits(settings)
    if not app.state.auth.login_enabled:
        log.warning("app.login_disabled", note="set admin username and password hash to enable")
    if not settings.rate_limit_enabled:
        log.warning("app.rate_limit_disabled")

    # The last one added is outermost: request context first (so even refusals get a request
    # id and an access line), then the per-address limit, then the body cap closest to routes.
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_request_bytes)
    install_address_rate_limit(app)
    install_request_context(app)
    install_error_handlers(app)

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        """Liveness probe; unauthenticated and free of side effects."""
        return {"status": "ok"}

    for router in (
        auth.router,
        catalog.router,
        testers.router,
        reports.router,
        sessions.router,
        actions.router,
    ):
        app.include_router(router)
    return app
