"""A real call to OpenAI. Skipped unless asked for: it spends a few cents and needs your key.

    make live

The key comes from your own `.env` (git-ignored) or the environment. This test prints token
counts only, never the key or request headers.
"""

import os

import pytest

from release_assistant.agent.http_backend import HttpToolBackend
from release_assistant.agent.messages import StopReason
from release_assistant.agent.openai_model import create_openai_model
from release_assistant.agent.runner import Agent
from release_assistant.config import Settings
from support import AGENT_KEY, http_client, make_app

pytestmark = pytest.mark.live


@pytest.fixture
def settings() -> Settings:
    if os.environ.get("RELEASE_ASSISTANT_RUN_LIVE") != "1":
        pytest.skip("set RELEASE_ASSISTANT_RUN_LIVE=1 (or run `make live`) to call OpenAI")
    loaded = Settings()
    if loaded.openai_api_key is None:
        pytest.skip("RELEASE_ASSISTANT_OPENAI_API_KEY is not set")
    return loaded


async def test_a_real_model_uses_a_tool_and_answers(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    model = create_openai_model(settings)

    async with http_client(make_app(), AGENT_KEY) as api:
        backend = await HttpToolBackend.open(api)
        run = await Agent(model, backend).respond([], "Which apps do I have? Answer briefly.")

    with capsys.disabled():
        print(
            f"\nlive run: model={settings.openai_model} steps={run.steps} tokens={run.usage.total}"
        )
    assert run.stop_reason is StopReason.ANSWERED
    assert run.tool_calls_made >= 1
    assert "Pocket Notes" in run.answer
