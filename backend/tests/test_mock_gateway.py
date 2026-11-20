from datetime import UTC, datetime

import pytest

from release_assistant.domain import ports
from release_assistant.domain.entities import BuildProcessingState, ReviewResponseState
from release_assistant.domain.errors import AlreadyExistsError, NotFoundError
from release_assistant.infrastructure.fixtures import sample_gateway


def test_mock_satisfies_every_port() -> None:
    gateway = sample_gateway()
    assert isinstance(gateway, ports.AppCatalog)
    assert isinstance(gateway, ports.TesterDirectory)
    assert isinstance(gateway, ports.ReviewInbox)


async def test_builds_are_scoped_to_app_and_newest_first() -> None:
    builds = await sample_gateway().list_builds("app-1")

    assert [b.build_number for b in builds] == ["42", "41"]
    assert builds[0].processing_state is BuildProcessingState.PROCESSING


async def test_unknown_app_raises_not_found() -> None:
    with pytest.raises(NotFoundError):
        await sample_gateway().list_builds("missing")


async def test_add_tester_normalizes_email_and_joins_group() -> None:
    gateway = sample_gateway()

    tester = await gateway.add_tester("group-1", "  New.Person@Example.com ")

    assert tester.email == "new.person@example.com"
    assert tester in await gateway.list_testers("group-1")


async def test_add_existing_tester_to_another_group_reuses_identity() -> None:
    gateway = sample_gateway()

    tester = await gateway.add_tester("group-1", "boris@example.com")

    assert tester.id == "tester-b"


async def test_add_duplicate_member_raises() -> None:
    with pytest.raises(AlreadyExistsError):
        await sample_gateway().add_tester("group-1", "anna@example.com")


async def test_remove_tester_only_affects_that_group() -> None:
    gateway = sample_gateway()

    await gateway.remove_tester("group-2", "tester-a")

    assert [t.id for t in await gateway.list_testers("group-2")] == ["tester-b"]
    assert [t.id for t in await gateway.list_testers("group-1")] == ["tester-a"]


async def test_remove_non_member_raises() -> None:
    with pytest.raises(NotFoundError):
        await sample_gateway().remove_tester("group-1", "tester-b")


async def test_reviews_filter_by_rating_and_date() -> None:
    gateway = sample_gateway()

    complaints = await gateway.list_reviews("app-1", max_rating=2)
    recent = await gateway.list_reviews("app-1", since=datetime(2026, 11, 15, tzinfo=UTC))

    assert [r.id for r in complaints] == ["review-2", "review-3"]
    assert [r.id for r in recent] == ["review-1", "review-2"]


async def test_respond_to_review_attaches_pending_response_once() -> None:
    gateway = sample_gateway()

    response = await gateway.respond_to_review("review-2", "Sorry, a fix is on the way.")

    assert response.state is ReviewResponseState.PENDING_PUBLISH
    reviews = await gateway.list_reviews("app-1", max_rating=1)
    assert reviews[0].response == response
    with pytest.raises(AlreadyExistsError):
        await gateway.respond_to_review("review-2", "Again")
