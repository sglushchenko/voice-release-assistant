"""FastAPI application factory."""

import structlog
from fastapi import FastAPI

from release_assistant.api.auth import Authenticator
from release_assistant.api.errors import install_error_handlers
from release_assistant.api.middleware import install_request_context
from release_assistant.api.routes import actions, catalog, reports, sessions, testers
from release_assistant.bootstrap import Application, build_mock_application
from release_assistant.config import Settings

log = structlog.get_logger(__name__)


def create_app(settings: Settings, application: Application | None = None) -> FastAPI:
    """Build the API.

    Args:
        settings: Credentials and backend choice.
        application: Prebuilt application, mainly for tests. Defaults to the one `settings`
            selects.
    """
    app = FastAPI(title="Voice Release Assistant", version="0.1.0")
    if application is None:
        log.warning("app.backend_selected", backend=settings.backend, note="mock data only")
        application = build_mock_application()
    app.state.application = application
    app.state.authenticator = Authenticator(settings.api_clients)

    install_request_context(app)
    install_error_handlers(app)

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        """Liveness probe; unauthenticated and free of side effects."""
        return {"status": "ok"}

    for router in (
        catalog.router,
        testers.router,
        reports.router,
        sessions.router,
        actions.router,
    ):
        app.include_router(router)
    return app
