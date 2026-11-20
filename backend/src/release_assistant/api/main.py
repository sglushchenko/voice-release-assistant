"""Entry point for the server: `uv run release-assistant`."""

import uvicorn

from release_assistant.api.app import create_app
from release_assistant.config import Settings
from release_assistant.observability.logging import configure_logging


def run() -> None:
    """Configure logging first, then start uvicorn with its own logging switched off.

    Uvicorn's default log setup writes plain text next to our JSON and duplicates the access
    line. With `log_config=None` its records propagate to the root logger and share our
    format; the access line comes from our middleware, which also knows the request id and
    the caller.
    """
    settings = Settings()
    configure_logging(settings)
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_config=None,
        access_log=False,
    )
