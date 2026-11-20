"""Tracks whether third-party text has entered a conversation (ADR 0003, provenance).

Services that hand untrusted content to a caller mark it here; the policy then tightens
approval for every later risky action in the same session. Marking at the point where the
content leaves the system means no caller has to remember to do it.
"""

from release_assistant.application.policy import ConfirmationContext


class TrustTracker:
    """One instance per conversation or session. Never reset it mid-session: once untrusted
    text has been seen, the model's context may carry its influence."""

    def __init__(self) -> None:
        self._sources: set[str] = set()

    def mark_untrusted(self, source: str) -> None:
        """Record that content from `source` (e.g. "customer_reviews") reached the caller."""
        self._sources.add(source)

    @property
    def untrusted_sources(self) -> tuple[str, ...]:
        """Sorted, for stable logs and tests."""
        return tuple(sorted(self._sources))

    def context(self) -> ConfirmationContext:
        """Pass this to `ActionService.propose` so the policy sees the session's trust state."""
        return ConfirmationContext(untrusted_content_seen=bool(self._sources))
