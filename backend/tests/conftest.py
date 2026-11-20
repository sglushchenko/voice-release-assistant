"""Shared test setup."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _one_event_loop_per_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Give every TestClient a single event loop for its whole life, like a real server.

    Without `with TestClient(...)`, Starlette starts a fresh event loop for every request.
    The rate limiter keeps asyncio locks and a background task in the loop of its first use,
    so the second request in a new loop could find a lock bound to a loop that no longer
    exists and fail at random ("Lock is bound to a different event loop"). Entering each
    client once, here, removes that test-only artifact; production runs one loop per process.
    """
    opened: list[TestClient] = []
    original_init = TestClient.__init__

    def init_and_enter(self: TestClient, *args: object, **kwargs: object) -> None:
        original_init(self, *args, **kwargs)  # type: ignore[arg-type]
        self.__enter__()
        opened.append(self)

    monkeypatch.setattr(TestClient, "__init__", init_and_enter)
    yield
    for client in reversed(opened):
        client.__exit__(None, None, None)
