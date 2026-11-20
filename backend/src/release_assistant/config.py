"""Runtime configuration, read from environment variables (12-factor).

Variables use the `RELEASE_ASSISTANT_` prefix and may also come from a local `.env` file,
which is git-ignored. See `.env.example` for the full list.
"""

from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Scope = Literal["read", "propose", "confirm:button", "confirm:voice"]


class ApiClient(BaseModel):
    """One credential allowed to call the HTTP API (ADR 0004).

    Args:
        subject: Stable name of the client (which app or agent is calling), used in logs.
        owner: The account the client acts for. Sessions and pending actions belong to the
            owner, so an agent client and the user's own app share them when they share an
            owner, and nobody else can touch them.
        key_sha256: Hex SHA-256 of the API key. Only the hash is stored; generate it with
            `printf '%s' "$KEY" | shasum -a 256`.
        scopes: What the client may do. Confirmation scopes are what let a client approve
            risky actions, so give them only to human-facing clients, never to an agent.
    """

    subject: str = Field(min_length=1, max_length=64)
    owner: str = Field(min_length=1, max_length=64)
    key_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scopes: frozenset[Scope]


class Settings(BaseSettings):
    """
    Args:
        log_level: Minimum severity that is emitted.
        log_json: JSON lines for production and log aggregators; set false for colored,
            human-readable output during local development.
        host: Interface the server binds to. Loopback by default; bind wider only behind a
            reverse proxy that terminates TLS.
        port: Port the server listens on.
        backend: Which App Store Connect implementation to use. Only the mock exists so far.
        api_clients: Credentials accepted by the HTTP API, as a JSON list in the environment.
            Empty means every request is rejected.
    """

    model_config = SettingsConfigDict(
        env_prefix="RELEASE_ASSISTANT_", env_file=".env", extra="ignore"
    )

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = True
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    backend: Literal["mock"] = "mock"
    api_clients: list[ApiClient] = []
