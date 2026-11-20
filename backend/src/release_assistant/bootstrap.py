"""Composition root: the only place that wires concrete implementations together.

Everything above this module depends on ports; swapping the mock gateway for the real App
Store Connect adapter, or the in-memory store for a database, is a change here and nowhere
else. Callers (HTTP API, voice agent, CLI) receive an `Application` and open a `Session`
per conversation.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from release_assistant.application import reviews, testers
from release_assistant.application.apps import AppsService
from release_assistant.application.builds import BuildsService
from release_assistant.application.pending import PendingAction, PendingActionStore
from release_assistant.application.policy import ConfirmationPolicy
from release_assistant.application.reviews import ReviewsService
from release_assistant.application.risk import ApprovalMode
from release_assistant.application.service import ActionService, Executed
from release_assistant.application.testers import BetaTesterService
from release_assistant.application.trust import TrustTracker
from release_assistant.domain.ports import AppStoreGateway
from release_assistant.infrastructure.fixtures import sample_gateway
from release_assistant.infrastructure.pending_store import InMemoryPendingActionStore


class Session:
    """State of one conversation: its trust level and the services that depend on it.

    Always propose writes through `propose`: it attaches the session's trust state, so a
    caller cannot forget to apply stricter approval after untrusted content was read.
    """

    def __init__(self, actions: ActionService, gateway: AppStoreGateway) -> None:
        self._actions = actions
        self.trust = TrustTracker()
        self.reviews = ReviewsService(gateway, self.trust)

    async def propose(self, name: str, arguments: dict[str, Any]) -> PendingAction | Executed:
        return await self._actions.propose(name, arguments, self.trust.context())


@dataclass(frozen=True)
class Application:
    """Shared, stateless services plus the confirmation machinery.

    Confirmation (`actions.confirm` / `actions.reject`) is not tied to a session: it comes
    from whatever channel the user acts on, such as a button in the client.
    """

    apps: AppsService
    builds: BuildsService
    testers: BetaTesterService
    actions: ActionService
    _gateway: AppStoreGateway

    def start_session(self) -> Session:
        return Session(self.actions, self._gateway)


def build_application(
    gateway: AppStoreGateway,
    *,
    store: PendingActionStore | None = None,
    approval_modes: Mapping[str, ApprovalMode] | None = None,
) -> Application:
    """Wire the application around `gateway`.

    Args:
        gateway: Mock or real App Store Connect adapter.
        store: Where pending actions live. Defaults to in memory, which loses them on
            restart and does not work across multiple processes.
        approval_modes: Overrides for the per-action defaults. The policy still clamps them
            to each action's risk floor, so an override can tighten but never loosen safely.
    """
    modes = {
        **testers.DEFAULT_APPROVAL_MODES,
        **reviews.DEFAULT_APPROVAL_MODES,
        **(approval_modes or {}),
    }
    actions = ActionService(
        definitions=[
            *testers.build_tester_actions(gateway),
            *reviews.build_review_actions(gateway),
        ],
        policy=ConfirmationPolicy(modes),
        store=store or InMemoryPendingActionStore(),
    )
    return Application(
        apps=AppsService(gateway),
        builds=BuildsService(gateway),
        testers=BetaTesterService(gateway),
        actions=actions,
        _gateway=gateway,
    )


def build_mock_application() -> Application:
    """Application over sample data: for demos, evals and local development."""
    return build_application(sample_gateway())
