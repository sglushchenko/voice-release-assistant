"""Runtime configuration, read from environment variables (12-factor).

Variables use the `RELEASE_ASSISTANT_` prefix and may also come from a local `.env` file,
which is git-ignored. See `.env.example` for the full list.
"""

from typing import Literal

from argon2 import extract_parameters
from argon2.exceptions import InvalidHashError
from limits import parse_many
from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Scope = Literal["read", "propose", "confirm:button", "confirm:voice"]

RateLimitPolicy = Literal["ip", "principal", "login", "propose", "reports", "reviews"]

# What each policy limits: `ip` every request from one address, `principal` every
# authenticated call by one caller, `login` password attempts per address, `propose` action
# proposals per caller, `reports` analytics reads per caller, `reviews` review reads per caller.
RATE_LIMIT_DEFAULTS: dict[RateLimitPolicy, str] = {
    "ip": "20/second",
    "principal": "20/second",
    "login": "10/minute",
    "propose": "30/minute",
    "reports": "20/minute",
    "reviews": "60/minute",
}


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
        trusted_proxies: Comma-separated addresses whose `X-Forwarded-For` is believed, so the
            real client address is used for limits and lockouts. Set to the reverse proxy.
        max_request_bytes: Largest request body accepted.
        rate_limit_enabled: Master switch for rate limiting (ADR 0006).
        rate_limit_storage: Where counters live, as a `limits` storage URI with an `async+`
            prefix: `async+memory://` (one process) or `async+redis://host:6379` (shared by
            several instances; needs `uv add "limits[async-redis]"`).
        rate_limits: Overrides for the default limits, by policy name, written as
            "10/minute" or several joined by ";" ("20/second;600/minute"). Policies are in
            `RATE_LIMIT_DEFAULTS`.
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
    trusted_proxies: str = "127.0.0.1"
    max_request_bytes: int = Field(default=65_536, ge=1024)
    rate_limit_enabled: bool = True
    rate_limit_storage: str = "async+memory://"
    rate_limits: dict[RateLimitPolicy, str] = {}
    admin_username: str | None = Field(default=None, min_length=1, max_length=64)
    admin_password_hash: SecretStr | None = None
    access_token_ttl_minutes: int = Field(default=480, ge=1, le=1440)
    login_max_failures: int = Field(default=5, ge=1, le=100)
    login_lockout_minutes: int = Field(default=15, ge=1, le=1440)

    @property
    def effective_rate_limits(self) -> dict[RateLimitPolicy, str]:
        """Defaults with the configured overrides applied."""
        return {**RATE_LIMIT_DEFAULTS, **self.rate_limits}

    @model_validator(mode="after")
    def _validate_rate_limits(self) -> "Settings":
        """Fail at startup, not at the first request, on a limit nobody can parse."""
        if not self.rate_limit_storage.startswith("async+"):
            raise ValueError("rate_limit_storage must be an async storage URI (async+...)")
        for policy, spec in self.rate_limits.items():
            try:
                parse_many(spec)
            except ValueError as error:
                raise ValueError(f"rate_limits[{policy}]: {error}") from error
        return self

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
