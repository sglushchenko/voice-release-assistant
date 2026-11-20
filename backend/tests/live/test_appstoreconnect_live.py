"""Real read-only calls to App Store Connect. Skipped unless asked for.

    make live-asc

Needs the three RELEASE_ASSISTANT_APPSTORE_* settings (your own `.env`) and
RELEASE_ASSISTANT_BACKEND=appstoreconnect. Only reads; prints counts and states, never names,
review text, tokens or the key.
"""

import os

import pytest

from release_assistant.config import Settings
from release_assistant.domain.errors import NotSupportedError
from release_assistant.infrastructure.appstoreconnect.factory import create_appstoreconnect_gateway

pytestmark = pytest.mark.live


@pytest.fixture
def settings() -> Settings:
    if os.environ.get("RELEASE_ASSISTANT_RUN_LIVE_ASC") != "1":
        pytest.skip("set RELEASE_ASSISTANT_RUN_LIVE_ASC=1 (or run `make live-asc`)")
    loaded = Settings(backend="appstoreconnect")
    return loaded


async def test_the_real_account_can_be_read(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    gateway = create_appstoreconnect_gateway(settings)

    apps = await gateway.list_apps()
    assert apps, "the key sees no apps: check its role and that the account has apps"
    app = apps[0]
    builds = await gateway.list_builds(app.id)
    versions = await gateway.list_versions(app.id)
    groups = await gateway.list_groups(app.id)
    reviews = await gateway.list_reviews(app.id)

    with capsys.disabled():
        print(
            f"\napps={len(apps)} builds={len(builds)} versions={len(versions)} "
            f"groups={len(groups)} reviews={len(reviews)} "
            f"states={sorted({v.apple_state or '-' for v in versions})}"
        )
    with pytest.raises(NotSupportedError):
        await gateway.add_tester("any", "nobody@example.com")
