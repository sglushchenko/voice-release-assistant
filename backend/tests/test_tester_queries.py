import pytest

from release_assistant.application.testers import BetaTesterService
from release_assistant.domain.errors import NotFoundError
from release_assistant.infrastructure.fixtures import sample_gateway


def service() -> BetaTesterService:
    return BetaTesterService(sample_gateway())


async def test_list_groups_is_scoped_to_app() -> None:
    groups = await service().list_groups("app-1")

    assert [g.name for g in groups] == ["Internal", "Public Beta"]
    assert await service().list_groups("app-2") == []


async def test_list_testers_of_group() -> None:
    testers = await service().list_testers("group-2")

    assert [t.email for t in testers] == ["anna@example.com", "boris@example.com"]


@pytest.mark.parametrize("email", ["boris@example.com", "  BORIS@Example.com "])
async def test_find_in_group_ignores_case_and_spaces(email: str) -> None:
    tester = await service().find_in_group("group-2", email)

    assert tester is not None
    assert tester.id == "tester-b"


async def test_find_in_group_returns_none_for_non_member() -> None:
    assert await service().find_in_group("group-1", "boris@example.com") is None


async def test_groups_of_lists_every_group_the_tester_is_in() -> None:
    groups = await service().groups_of("app-1", "anna@example.com")

    assert [g.name for g in groups] == ["Internal", "Public Beta"]
    assert [g.name for g in await service().groups_of("app-1", "boris@example.com")] == [
        "Public Beta"
    ]


async def test_unknown_group_propagates_not_found() -> None:
    with pytest.raises(NotFoundError):
        await service().list_testers("missing")
