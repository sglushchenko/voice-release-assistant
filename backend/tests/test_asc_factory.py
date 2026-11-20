"""Configuring the real backend: what is checked at startup, and what the app then does."""

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from asc_support import generate_key_pem, write_key_file
from release_assistant.application.reviews import RESPOND_TO_REVIEW
from release_assistant.application.testers import ADD_TESTER
from release_assistant.bootstrap import build_application_from_settings
from release_assistant.config import ConfigurationError, Settings
from release_assistant.domain.errors import NotSupportedError
from release_assistant.domain.ports import AppStoreGateway
from release_assistant.infrastructure.appstoreconnect.factory import create_appstoreconnect_gateway


def settings(key_path: Path | None, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "backend": "appstoreconnect",
        "appstore_key_id": "TESTKEY123",
        "appstore_issuer_id": "issuer-uuid",
        "appstore_private_key_path": key_path,
        **overrides,
    }
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_the_default_backend_is_the_mock() -> None:
    assert Settings(_env_file=None).backend == "mock"


@pytest.mark.parametrize(
    "missing", ["appstore_key_id", "appstore_issuer_id", "appstore_private_key_path"]
)
def test_the_real_backend_names_every_missing_setting(missing: str, tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match=f"RELEASE_ASSISTANT_{missing.upper()}"):
        settings(write_key_file(tmp_path, generate_key_pem()), **{missing: None})


def test_a_good_configuration_builds_a_complete_gateway_without_touching_the_network(
    tmp_path: Path,
) -> None:
    gateway = create_appstoreconnect_gateway(settings(write_key_file(tmp_path, generate_key_pem())))

    assert isinstance(gateway, AppStoreGateway)


def test_an_unsafe_key_file_stops_startup(tmp_path: Path) -> None:
    key = write_key_file(tmp_path, generate_key_pem(), mode=0o644)

    with pytest.raises(ConfigurationError, match="readable by other users"):
        create_appstoreconnect_gateway(settings(key))


def test_a_missing_key_file_stops_startup(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="not found"):
        create_appstoreconnect_gateway(settings(tmp_path / "gone.p8"))


async def test_without_a_vendor_number_sales_say_how_to_enable_them(tmp_path: Path) -> None:
    gateway = create_appstoreconnect_gateway(settings(write_key_file(tmp_path, generate_key_pem())))

    with pytest.raises(NotSupportedError, match="VENDOR_NUMBER"):
        await gateway.list_sales(date(2026, 11, 1), date(2026, 11, 2))


async def test_the_real_backend_application_refuses_every_write_up_front(tmp_path: Path) -> None:
    app = build_application_from_settings(settings(write_key_file(tmp_path, generate_key_pem())))
    _, session = app.sessions.create("alice")

    for name in (ADD_TESTER, RESPOND_TO_REVIEW):
        with pytest.raises(NotSupportedError, match="not supported by this backend yet"):
            await session.propose(name, {})


def test_the_mock_application_is_still_the_default_when_asked_for_by_settings() -> None:
    app = build_application_from_settings(Settings(_env_file=None))

    assert app.sessions is not None
