import pytest

from asc_support import resource
from release_assistant.domain.entities import AppStoreVersionState
from release_assistant.infrastructure.appstoreconnect.mapping import map_version, map_version_state

# Every value Apple documents for either state field (OpenAPI 4.6.1).
APPLE_APP_STORE_STATES = [
    "ACCEPTED",
    "DEVELOPER_REMOVED_FROM_SALE",
    "DEVELOPER_REJECTED",
    "IN_REVIEW",
    "INVALID_BINARY",
    "METADATA_REJECTED",
    "PENDING_APPLE_RELEASE",
    "PENDING_CONTRACT",
    "PENDING_DEVELOPER_RELEASE",
    "PREPARE_FOR_SUBMISSION",
    "PREORDER_READY_FOR_SALE",
    "PROCESSING_FOR_APP_STORE",
    "READY_FOR_REVIEW",
    "READY_FOR_SALE",
    "REJECTED",
    "REMOVED_FROM_SALE",
    "WAITING_FOR_EXPORT_COMPLIANCE",
    "WAITING_FOR_REVIEW",
    "REPLACED_WITH_NEW_VERSION",
    "NOT_APPLICABLE",
]
APPLE_APP_VERSION_STATES = [
    "ACCEPTED",
    "DEVELOPER_REJECTED",
    "IN_REVIEW",
    "INVALID_BINARY",
    "METADATA_REJECTED",
    "PENDING_APPLE_RELEASE",
    "PENDING_DEVELOPER_RELEASE",
    "PREPARE_FOR_SUBMISSION",
    "PROCESSING_FOR_DISTRIBUTION",
    "READY_FOR_DISTRIBUTION",
    "READY_FOR_REVIEW",
    "REJECTED",
    "REPLACED_WITH_NEW_VERSION",
    "WAITING_FOR_EXPORT_COMPLIANCE",
    "WAITING_FOR_REVIEW",
]


@pytest.mark.parametrize("state", sorted(set(APPLE_APP_STORE_STATES + APPLE_APP_VERSION_STATES)))
def test_every_state_apple_documents_maps_to_something(state: str) -> None:
    assert isinstance(map_version_state(state), AppStoreVersionState)


@pytest.mark.parametrize(
    ("apple", "ours"),
    [
        ("WAITING_FOR_REVIEW", AppStoreVersionState.WAITING_FOR_REVIEW),
        ("IN_REVIEW", AppStoreVersionState.IN_REVIEW),
        ("PREPARE_FOR_SUBMISSION", AppStoreVersionState.PREPARE_FOR_SUBMISSION),
        ("PENDING_DEVELOPER_RELEASE", AppStoreVersionState.PENDING_DEVELOPER_RELEASE),
        ("READY_FOR_SALE", AppStoreVersionState.READY_FOR_SALE),
        ("READY_FOR_DISTRIBUTION", AppStoreVersionState.READY_FOR_SALE),
        ("REJECTED", AppStoreVersionState.REJECTED),
        ("METADATA_REJECTED", AppStoreVersionState.REJECTED),
        ("DEVELOPER_REJECTED", AppStoreVersionState.REJECTED),
        ("INVALID_BINARY", AppStoreVersionState.REJECTED),
    ],
)
def test_the_states_the_assistant_reasons_about_map_exactly(
    apple: str, ours: AppStoreVersionState
) -> None:
    assert map_version_state(apple) is ours


@pytest.mark.parametrize("apple", ["ACCEPTED", "PENDING_APPLE_RELEASE", "SOMETHING_NEW", None, ""])
def test_everything_else_is_other_never_a_wrong_guess(apple: str | None) -> None:
    assert map_version_state(apple) is AppStoreVersionState.OTHER


def test_the_newer_state_field_wins_and_the_exact_value_is_kept() -> None:
    both = resource(
        "appStoreVersions",
        "v",
        {"versionString": "2.0", "appStoreState": "REJECTED", "appVersionState": "IN_REVIEW"},
    )
    legacy_only = resource(
        "appStoreVersions", "v", {"versionString": "2.0", "appStoreState": "REJECTED"}
    )

    assert map_version(both, "app").apple_state == "IN_REVIEW"
    assert map_version(both, "app").state is AppStoreVersionState.IN_REVIEW
    assert map_version(legacy_only, "app").state is AppStoreVersionState.REJECTED


def test_a_version_without_any_state_is_other() -> None:
    version = map_version(resource("appStoreVersions", "v", {"versionString": "2.0"}), "app")

    assert version.state is AppStoreVersionState.OTHER
    assert version.apple_state is None
