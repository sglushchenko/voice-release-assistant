import pytest

from release_assistant.application.builds import BuildsService
from release_assistant.domain.entities import App, BuildProcessingState
from release_assistant.domain.errors import NotFoundError
from release_assistant.infrastructure.fixtures import sample_gateway
from release_assistant.infrastructure.mock_gateway import MockAppStoreGateway


def service() -> BuildsService:
    return BuildsService(sample_gateway())


async def test_latest_is_the_most_recent_upload() -> None:
    latest = await service().latest("app-1")

    assert latest is not None
    assert latest.build_number == "42"


async def test_latest_is_none_for_app_without_builds() -> None:
    gateway = MockAppStoreGateway(
        apps=[App(id="app-1", name="Empty", bundle_id="com.example.empty")]
    )

    assert await BuildsService(gateway).latest("app-1") is None


async def test_filter_by_state() -> None:
    builds = await service().list_builds("app-1", states={BuildProcessingState.PROCESSING})

    assert [b.build_number for b in builds] == ["42"]


async def test_limit_keeps_newest() -> None:
    builds = await service().list_builds("app-1", limit=1)

    assert [b.build_number for b in builds] == ["42"]


@pytest.mark.parametrize("limit", [0, -1])
async def test_non_positive_limit_is_rejected(limit: int) -> None:
    with pytest.raises(ValueError, match="limit"):
        await service().list_builds("app-1", limit=limit)


async def test_problem_builds_are_failed_or_invalid_only() -> None:
    assert await service().problem_builds("app-1") == []

    problems = await service().problem_builds("app-2")

    assert [b.id for b in problems] == ["build-3"]


async def test_unknown_app_propagates_not_found() -> None:
    with pytest.raises(NotFoundError):
        await service().latest("missing")
