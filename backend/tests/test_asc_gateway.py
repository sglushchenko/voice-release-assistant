"""The App Store Connect gateway against a fake service that speaks Apple's JSON:API shapes."""

from datetime import UTC, datetime

import httpx
import pytest

from asc_support import (
    APPS,
    BUILDS,
    GROUP,
    GROUPS,
    REVIEWS,
    TESTERS,
    VERSIONS,
    make_gateway,
    not_found,
    page,
    resource,
    to_one,
)
from release_assistant.application.analytics import AnalyticsService
from release_assistant.application.builds import BuildsService
from release_assistant.application.engagement import EngagementService
from release_assistant.domain.entities import (
    AppStoreVersionState,
    BuildProcessingState,
    ReviewResponseState,
)
from release_assistant.domain.errors import NotFoundError, NotSupportedError, UpstreamError
from release_assistant.domain.ports import AppStoreGateway
from release_assistant.infrastructure.appstoreconnect.gateway import AppStoreConnectGateway
from release_assistant.infrastructure.composite_gateway import CompositeGateway, UnsupportedReports


def router(routes: dict[str, httpx.Response | dict]) -> tuple:  # type: ignore[type-arg]
    """A fake service answering by path; records every request."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        answer = routes.get(request.url.path, not_found())
        return answer if isinstance(answer, httpx.Response) else httpx.Response(200, json=answer)

    return handler, seen


# --- apps and versions -----------------------------------------------------------------


async def test_apps_are_listed_at_the_largest_page_size() -> None:
    handler, seen = router({"/v1/apps": APPS})

    apps = await make_gateway(handler).list_apps()

    assert [(a.id, a.name, a.bundle_id) for a in apps] == [
        ("1001", "Pocket Notes", "com.example.notes"),
        ("1002", "Habit Garden", "com.example.habits"),
    ]
    assert seen[0].url.params["limit"] == "200"


async def test_versions_keep_apples_exact_state_next_to_the_mapped_one() -> None:
    handler, _ = router({"/v1/apps/1001/appStoreVersions": VERSIONS})

    versions = await make_gateway(handler).list_versions("1001")

    assert [(v.version_string, v.state, v.apple_state) for v in versions] == [
        ("1.4.0", AppStoreVersionState.WAITING_FOR_REVIEW, "WAITING_FOR_REVIEW"),
        ("1.3.0", AppStoreVersionState.REJECTED, "METADATA_REJECTED"),
        ("1.2.0", AppStoreVersionState.OTHER, "ACCEPTED"),
    ]


async def test_versions_of_an_unknown_app_are_not_found() -> None:
    handler, _ = router({})

    with pytest.raises(NotFoundError):
        await make_gateway(handler).list_versions("999")


# --- builds ----------------------------------------------------------------------------


async def test_builds_ask_apple_for_newest_first_with_the_marketing_version_included() -> None:
    handler, seen = router({"/v1/builds": BUILDS})

    builds = await make_gateway(handler).list_builds("1001")

    params = seen[0].url.params
    assert params["filter[app]"] == "1001"
    assert params["sort"] == "-uploadedDate"
    assert params["include"] == "preReleaseVersion"
    assert [(b.build_number, b.version, b.processing_state) for b in builds] == [
        ("42", "1.4.0", BuildProcessingState.PROCESSING),
        ("41", "1.4.0", BuildProcessingState.VALID),
    ]
    assert builds[0].uploaded_at == datetime(2026, 11, 19, 23, 30, tzinfo=UTC)


async def test_an_empty_build_list_checks_whether_the_app_exists() -> None:
    handler, seen = router({"/v1/builds": page([])})

    with pytest.raises(NotFoundError):
        await make_gateway(handler).list_builds("999")

    assert [r.url.path for r in seen] == ["/v1/builds", "/v1/apps/999"]


async def test_an_existing_app_with_no_builds_is_an_empty_list() -> None:
    handler, _ = router(
        {"/v1/builds": page([]), "/v1/apps/1001": {"data": resource("apps", "1001", {})}}
    )

    assert await make_gateway(handler).list_builds("1001") == []


async def test_an_unknown_processing_state_is_reported_not_guessed() -> None:
    bad = page(
        [
            resource(
                "builds",
                "b",
                {
                    "version": "1",
                    "uploadedDate": "2026-11-18T09:00:00Z",
                    "processingState": "MYSTERY",
                },
            )
        ]
    )
    handler, _ = router({"/v1/builds": bad})

    with pytest.raises(UpstreamError, match="unknown build processing state"):
        await make_gateway(handler).list_builds("1001")


async def test_the_services_work_unchanged_over_the_real_gateway() -> None:
    handler, _ = router({"/v1/builds": BUILDS})
    service = BuildsService(make_gateway(handler))

    latest = await service.latest("1001")

    assert latest is not None
    assert latest.build_number == "42"


# --- TestFlight ------------------------------------------------------------------------


async def test_groups_and_testers_are_mapped() -> None:
    handler, _ = router(
        {"/v1/apps/1001/betaGroups": GROUPS, "/v1/betaGroups/g1/betaTesters": TESTERS}
    )
    gateway = make_gateway(handler)

    groups = await gateway.list_groups("1001")
    testers = await gateway.list_testers("g1")

    assert [(g.id, g.app_id, g.name) for g in groups] == [
        ("g1", "1001", "Internal"),
        ("g2", "1001", "Public Beta"),
    ]
    assert [(t.email, t.first_name, t.last_name) for t in testers] == [
        ("anna@example.com", "Anna", None),
        ("boris@example.com", None, None),
    ]


async def test_a_group_found_by_id_learns_its_app_from_the_included_relationship() -> None:
    handler, seen = router({"/v1/betaGroups/g1": GROUP})

    group = await make_gateway(handler).get_group("g1")

    assert (group.id, group.app_id, group.name) == ("g1", "1001", "Internal")
    assert seen[0].url.params["include"] == "app"


async def test_a_group_that_does_not_say_its_app_is_an_upstream_error() -> None:
    handler, _ = router(
        {"/v1/betaGroups/g1": {"data": resource("betaGroups", "g1", {"name": "x"}, {})}}
    )

    with pytest.raises(UpstreamError, match="which app"):
        await make_gateway(handler).get_group("g1")


# --- reviews ---------------------------------------------------------------------------


async def test_reviews_come_newest_first_with_their_developer_responses() -> None:
    handler, seen = router({"/v1/apps/1001/customerReviews": REVIEWS})

    reviews = await make_gateway(handler).list_reviews("1001")

    params = seen[0].url.params
    assert params["sort"] == "-createdDate"
    assert params["include"] == "response"
    assert "filter[rating]" not in params
    crash, love = reviews
    assert (crash.rating, crash.territory, crash.app_id) == (1, "DEU", "1001")
    assert crash.response is not None
    assert (crash.response.body, crash.response.state) == ("Sorry!", ReviewResponseState.PUBLISHED)
    assert love.response is None


async def test_complaints_are_requested_as_a_comma_separated_list_of_ratings() -> None:
    handler, seen = router({"/v1/apps/1001/customerReviews": page([])})

    await make_gateway(handler).list_reviews("1001", max_rating=3)

    assert seen[0].url.params["filter[rating]"] == "1,2,3"


async def test_reviews_can_be_limited_to_those_since_a_date() -> None:
    handler, _ = router({"/v1/apps/1001/customerReviews": REVIEWS})

    reviews = await make_gateway(handler).list_reviews(
        "1001", since=datetime(2026, 11, 15, tzinfo=UTC)
    )

    assert [r.id for r in reviews] == ["r2"]


async def test_a_review_fetched_by_id_does_not_know_its_app() -> None:
    document = {"data": REVIEWS["data"][0], "included": REVIEWS["included"]}
    handler, _ = router({"/v1/customerReviews/r2": document})

    review = await make_gateway(handler).get_review("r2")

    assert review.id == "r2"
    assert review.app_id is None
    assert review.response is not None


async def test_an_unknown_review_is_not_found() -> None:
    handler, _ = router({})

    with pytest.raises(NotFoundError):
        await make_gateway(handler).get_review("nope")


# --- what is deliberately not there ----------------------------------------------------


async def test_every_write_refuses_plainly() -> None:
    gateway = make_gateway(router({})[0])

    for call in (
        gateway.add_tester("g1", "a@b.co"),
        gateway.remove_tester("g1", "t1"),
        gateway.respond_to_review("r1", "hi"),
    ):
        with pytest.raises(NotSupportedError, match="not enabled yet"):
            await call


async def test_reports_say_they_are_not_supported_yet() -> None:
    reads = make_gateway(router({"/v1/apps": APPS})[0])
    reports = UnsupportedReports("App Store Connect")
    gateway = CompositeGateway(
        catalog=reads, builds=reads, testers=reads, reviews=reads, sales=reports, engagement=reports
    )

    assert isinstance(gateway, AppStoreGateway)
    with pytest.raises(
        NotSupportedError, match="sales and engagement reports are not supported yet"
    ):
        await AnalyticsService(gateway, gateway).monthly_report(2026, 10)
    with pytest.raises(NotSupportedError, match="not supported yet"):
        await EngagementService(gateway, gateway).recent(7)
    assert len(await gateway.list_apps()) == 2


def test_the_reading_part_satisfies_the_read_ports() -> None:
    from release_assistant.domain import ports

    gateway = make_gateway(router({})[0])

    assert isinstance(gateway, AppStoreConnectGateway)
    assert isinstance(gateway, ports.AppCatalog)
    assert isinstance(gateway, ports.BuildCatalog)
    assert isinstance(gateway, ports.TesterDirectory)
    assert isinstance(gateway, ports.ReviewInbox)


def test_one_to_one_helper_shape() -> None:
    assert to_one("apps", "1") == {"data": {"type": "apps", "id": "1"}}
