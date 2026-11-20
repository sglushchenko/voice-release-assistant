"""Structured logging setup.

Call `configure_logging` once, from an entry point (API server, CLI, eval runner), never
from library code. Application code just does `structlog.get_logger(__name__)`.

Conventions:
- Events are `snake_case` names like "action.proposed", with data as key-value pairs.
- Log identifiers (action_id, review_id), never user content: no review text, reply bodies,
  emails or action arguments. `redact_sensitive` is a safety net for mistakes, not a licence.
"""

import logging
import sys
from collections.abc import MutableMapping
from typing import IO, Any

import structlog

from release_assistant.config import Settings

SENSITIVE_KEYS = frozenset(
    {"arguments", "body", "email", "first_name", "last_name", "token", "password", "authorization"}
)
REDACTED = "[redacted]"


def redact_sensitive(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Replace values of keys that could carry personal or secret data."""
    for key in SENSITIVE_KEYS & event_dict.keys():
        event_dict[key] = REDACTED
    return event_dict


def configure_logging(settings: Settings, *, stream: IO[str] | None = None) -> None:
    """Route structlog and standard-library logging through one pipeline.

    Standard-library records (uvicorn, httpx, SDKs) get the same format, so a log
    aggregator sees one consistent stream.

    Args:
        settings: Source of level and output format.
        stream: Where to write; defaults to stderr. Overridden by tests.
    """
    shared: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_sensitive,
    ]
    renderer: structlog.typing.Processor
    if settings.log_json:
        renderer = structlog.processors.JSONRenderer()
        finishing: list[structlog.typing.Processor] = [structlog.processors.format_exc_info]
    else:
        renderer = structlog.dev.ConsoleRenderer()
        finishing = []

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )

    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                *finishing,
                renderer,
            ],
        )
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(settings.log_level)
