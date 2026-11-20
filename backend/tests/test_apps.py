import pytest

from release_assistant.application.apps import AppsService
from release_assistant.domain.entities import AppStoreVersionState
from release_assistant.domain.errors import NotFoundError
from release_assistant.infrastructure.fixtures import sample_gateway


def service() -> AppsService:
    return AppsService(sample_gateway())


@pytest.mark.parametrize("query", ["Pocket Notes", "  pocket notes ", "POCKET NOTES"])
async def test_find_by_exact_name_ignores_case_and_spaces(query: str) -> None:
    apps = await service().find_by_name(query)

    assert [a.id for a in apps] == ["app-1"]


async def test_find_by_partial_name_or_bundle_id() -> None:
    assert [a.id for a in await service().find_by_name("garden")] == ["app-2"]
    assert [a.id for a in await service().find_by_name("pocketnotes")] == ["app-1"]


async def test_ambiguous_query_returns_every_candidate() -> None:
    apps = await service().find_by_name("example")

    assert {a.id for a in apps} == {"app-1", "app-2"}


@pytest.mark.parametrize("query", ["", "   ", "nonexistent"])
async def test_find_returns_nothing_for_blank_or_unknown(query: str) -> None:
    assert await service().find_by_name(query) == []


async def test_list_versions_can_filter_by_state() -> None:
    versions = await service().list_versions("app-2", states={AppStoreVersionState.READY_FOR_SALE})

    assert [v.version_string for v in versions] == ["1.9.2"]


async def test_in_review_spans_apps_and_carries_the_app() -> None:
    entries = await service().in_review()

    assert [(e.app.name, e.version.version_string) for e in entries] == [("Pocket Notes", "1.4.0")]


async def test_rejected_lists_versions_needing_a_response() -> None:
    entries = await service().rejected()

    assert [(e.app.id, e.version.id) for e in entries] == [("app-2", "ver-3")]


async def test_unknown_app_propagates_not_found() -> None:
    with pytest.raises(NotFoundError):
        await service().list_versions("missing")
