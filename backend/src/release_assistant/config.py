"""Runtime configuration, read from environment variables (12-factor).

Variables use the `RELEASE_ASSISTANT_` prefix and may also come from a local `.env` file,
which is git-ignored. See `.env.example` for the full list.
"""

from typing import Literal

from argon2 import extract_parameters
from argon2.exceptions import InvalidHashError
from pydantic import BaseModel, Field, SecretStr, model_validator
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
        api_clients: Static API keys (for the agent), as a JSON list in the environment.
        admin_username: The human account; also the owner every credential must share.
        admin_password_hash: argon2id hash of the admin password, never the password itself.
            Create it with `release-assistant-hash-password`. Both admin settings unset
            disables password login.
        access_token_ttl_minutes: How long a login stays valid without being used; each use
            extends it.
        login_max_failures: Failed logins from one address before it is locked out.
        login_lockout_minutes: How long that lockout lasts.
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
    admin_username: str | None = Field(default=None, min_length=1, max_length=64)
    admin_password_hash: SecretStr | None = None
    access_token_ttl_minutes: int = Field(default=480, ge=1, le=1440)
    login_max_failures: int = Field(default=5, ge=1, le=100)
    login_lockout_minutes: int = Field(default=15, ge=1, le=1440)

    @model_validator(mode="after")
    def _validate_admin(self) -> "Settings":
        """Refuse to start on a configuration that would silently weaken or break login."""
        if (self.admin_username is None) != (self.admin_password_hash is None):
            raise ValueError("admin_username and admin_password_hash must be set together")
        if self.admin_username is None or self.admin_password_hash is None:
            return self
        try:
            extract_parameters(self.admin_password_hash.get_secret_value())
        except InvalidHashError as error:
            raise ValueError("admin_password_hash is not a valid argon2 hash") from error
        foreign = [c.subject for c in self.api_clients if c.owner != self.admin_username]
        if foreign:
            raise ValueError(
                f"API clients {foreign} belong to a different owner than the admin "
                f"'{self.admin_username}' and could not share actions with them"
            )
        return self
