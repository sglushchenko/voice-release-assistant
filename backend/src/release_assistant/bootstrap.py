"""Composition root: the only place that wires concrete implementations together.

Everything above this module depends on ports; swapping the mock gateway for the real App
Store Connect adapter, or the in-memory store for a database, is a change here and nowhere
else. Callers (HTTP API, voice agent, CLI) receive an `Application` and open a `Session`
per conversation through `Application.sessions`.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from release_assistant.application import reviews, testers
from release_assistant.application.analytics import AnalyticsService
from release_assistant.application.apps import AppsService
from release_assistant.application.builds import BuildsService
from release_assistant.application.engagement import EngagementService
from release_assistant.application.pending import PendingAction, PendingActionStore
from release_assistant.application.policy import ConfirmationPolicy
from release_assistant.application.reviews import ReviewsService
from release_assistant.application.risk import ApprovalMode
from release_assistant.application.service import ActionService, Executed
from release_assistant.application.sessions import SessionRegistry
from release_assistant.application.testers import BetaTesterService
from release_assistant.application.trust import TrustTracker
from release_assistant.config import Settings
from release_assistant.domain.ports import AppStoreGateway
from release_assistant.infrastructure.appstoreconnect.factory import create_appstoreconnect_gateway
from release_assistant.infrastructure.fixtures import sample_gateway
from release_assistant.infrastructure.pending_store import InMemoryPendingActionStore

WRITE_ACTIONS = (testers.ADD_TESTER, testers.REMOVE_TESTER, reviews.RESPOND_TO_REVIEW)


class Session:
    """State of one conversation for one account: its trust level and dependent services.

    Always propose writes through `propose`: it attaches the session's trust state and the
    owner, so a caller cannot forget to apply stricter approval after untrusted content was
    read, nor file an action under someone else's account.
    """

    def __init__(self, owner: str, actions: ActionService, gateway: AppStoreGateway) -> None:
        self.owner = owner
        self._actions = actions
        self.trust = TrustTracker()
        self.reviews = ReviewsService(gateway, self.trust)

    async def propose(self, name: str, arguments: dict[str, Any]) -> PendingAction | Executed:
        return await self._actions.propose(
            name, arguments, owner=self.owner, context=self.trust.context()
        )


@dataclass(frozen=True)
class Application:
    """Shared services plus the confirmation machinery and the session registry.

    Confirmation (`actions.confirm` / `actions.reject`) is not tied to a session: it comes
    from whatever channel the user acts on, such as a button in the client, and is checked
    against the action's owner.
    """

    apps: AppsService
    builds: BuildsService
    testers: BetaTesterService
    analytics: AnalyticsService
    engagement: EngagementService
    actions: ActionService
    sessions: SessionRegistry[Session]


def build_application(
    gateway: AppStoreGateway,
    *,
    store: PendingActionStore | None = None,
    approval_modes: Mapping[str, ApprovalMode] | None = None,
    writes_enabled: bool = True,
) -> Application:
    """Wire the application around `gateway`.

    Args:
        gateway: Mock or real App Store Connect adapter.
        store: Where pending actions live. Defaults to in memory, which loses them on
            restart and does not work across multiple processes.
        writes_enabled: False for a backend that cannot change anything yet: proposing any write
            is then refused up front as not supported.
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
        disabled_actions=() if writes_enabled else WRITE_ACTIONS,
    )
    return Application(
        apps=AppsService(gateway),
        builds=BuildsService(gateway),
        testers=BetaTesterService(gateway),
        analytics=AnalyticsService(gateway, gateway),
        engagement=EngagementService(gateway, gateway),
        actions=actions,
        sessions=SessionRegistry(lambda owner: Session(owner, actions, gateway)),
    )


def build_application_from_settings(settings: Settings) -> Application:
    """The application the configuration asks for.

    `mock` is sample data with every capability. `appstoreconnect` is the real service, which
    for now can only read: writes are refused up front and reports say they are not supported.
    """
    if settings.backend == "mock":
        return build_mock_application()
    return build_application(create_appstoreconnect_gateway(settings), writes_enabled=False)


def build_mock_application() -> Application:
    """Application over sample data: for demos, evals and local development."""
    return build_application(sample_gateway())
