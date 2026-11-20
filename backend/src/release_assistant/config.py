"""Runtime configuration, read from environment variables (12-factor).

Variables use the `RELEASE_ASSISTANT_` prefix and may also come from a local `.env` file,
which is git-ignored. See `.env.example` for the full list.
"""

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Args:
        log_level: Minimum severity that is emitted.
        log_json: JSON lines for production and log aggregators; set false for colored,
            human-readable output during local development.
    """

    model_config = SettingsConfigDict(
        env_prefix="RELEASE_ASSISTANT_", env_file=".env", extra="ignore"
    )

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = True
