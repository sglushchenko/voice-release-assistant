"""Entry point for the server: `uv run release-assistant`."""

import getpass
import sys

import uvicorn

from release_assistant.api.app import create_app
from release_assistant.api.login import make_password_hash
from release_assistant.config import Settings
from release_assistant.observability.logging import configure_logging

_MIN_PASSWORD_LENGTH = 12


def hash_password() -> None:
    """`uv run release-assistant-hash-password`: prompt for a password and print its argon2id
    hash for RELEASE_ASSISTANT_ADMIN_PASSWORD_HASH. Prompting (not an argument) keeps the
    password out of shell history and process listings."""
    password = getpass.getpass("Password: ")
    if len(password) < _MIN_PASSWORD_LENGTH:
        sys.exit(f"Use at least {_MIN_PASSWORD_LENGTH} characters")
    if getpass.getpass("Repeat: ") != password:
        sys.exit("Passwords do not match")
    print(make_password_hash(password))


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
        proxy_headers=True,
        forwarded_allow_ips=settings.trusted_proxies,
        log_config=None,
        access_log=False,
    )
