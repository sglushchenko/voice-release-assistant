"""In-memory PendingActionStore for tests, demos and single-process runs."""

from release_assistant.application.pending import PendingAction, PendingStatus
from release_assistant.application.risk import ConfirmationChannel


class InMemoryPendingActionStore:
    def __init__(self) -> None:
        self._actions: dict[str, PendingAction] = {}

    async def add(self, action: PendingAction) -> None:
        self._actions[action.id] = action

    async def get(self, action_id: str) -> PendingAction | None:
        return self._actions.get(action_id)

    async def transition(
        self,
        action_id: str,
        *,
        expected: PendingStatus,
        new: PendingStatus,
        confirmed_via: ConfirmationChannel | None = None,
    ) -> PendingAction | None:
        current = self._actions.get(action_id)
        if current is None or current.status is not expected:
            return None
        updated = current.model_copy(update={"status": new, "confirmed_via": confirmed_via})
        self._actions[action_id] = updated
        return updated
