"""A fresh, isolated world for each trial: the real API over sample data, nothing shared."""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from release_assistant.api.app import create_app
from release_assistant.bootstrap import build_application
from release_assistant.config import ApiClient, Settings
from release_assistant.domain.entities import CustomerReview
from release_assistant.evals.cases import EvalCase
from release_assistant.infrastructure.fixtures import sample_gateway
from release_assistant.infrastructure.mock_gateway import MockAppStoreGateway

_AGENT_KEY = "evaluation-agent-key"  # a throwaway credential for an in-process server


@dataclass
class Environment:
    """The API running in process, and a client holding only the agent's credential.

    Args:
        gateway: The mock App Store Connect behind the API, kept to take state snapshots.
        client: HTTP client authenticated as the agent (read and propose, never confirm).
    """

    gateway: MockAppStoreGateway
    client: httpx.AsyncClient

    def snapshot(self) -> dict[str, Any]:
        return self.gateway.snapshot()

    async def actions(self) -> list[dict[str, Any]]:
        """Every action the server holds for the account, whatever its status."""
        response = await self.client.get("/v1/actions")
        response.raise_for_status()
        actions: list[dict[str, Any]] = response.json()
        return actions

    async def close(self) -> None:
        await self.client.aclose()


def build_environment(case: EvalCase) -> Environment:
    """Sample data plus the case's own reviews, behind the real API with rate limits off
    (an evaluation is not an abuser, and 429s would only add noise to the measurement)."""
    base = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
    injected = [
        CustomerReview(
            id=f"eval-review-{index}",
            app_id="app-1",
            rating=5,
            title="Great app",
            body=body,
            territory="USA",
            created_at=base + timedelta(minutes=index),
        )
        for index, body in enumerate(case.reviews, start=1)
    ]
    gateway = sample_gateway(extra_reviews=injected)
    settings = Settings(
        _env_file=None,
        rate_limit_enabled=False,
        admin_username=None,
        admin_password_hash=None,
        api_clients=[
            ApiClient(
                subject="eval-agent",
                owner="eval-owner",
                key_sha256=hashlib.sha256(_AGENT_KEY.encode()).hexdigest(),
                scopes=frozenset({"read", "propose"}),
            )
        ],
    )
    app = create_app(settings, build_application(gateway))
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://evaluation",
        headers={"Authorization": f"Bearer {_AGENT_KEY}"},
    )
    return Environment(gateway=gateway, client=client)
