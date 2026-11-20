"""Building the real gateway from configuration, failing at startup if it cannot work."""

import httpx

from release_assistant.config import ConfigurationError, Settings
from release_assistant.domain.ports import AppStoreGateway
from release_assistant.infrastructure.appstoreconnect.auth import TokenProvider, load_private_key
from release_assistant.infrastructure.appstoreconnect.client import AscClient
from release_assistant.infrastructure.appstoreconnect.gateway import AppStoreConnectGateway
from release_assistant.infrastructure.composite_gateway import CompositeGateway, UnsupportedReports


def create_appstoreconnect_gateway(settings: Settings) -> AppStoreGateway:
    """The real service for what it supports (reading apps, builds, versions, TestFlight and
    reviews), with sales and engagement reports answering "not supported yet".

    Raises ConfigurationError when a setting is missing or the key file is unusable, so a
    misconfigured server never starts.
    """
    if (
        settings.appstore_key_id is None
        or settings.appstore_issuer_id is None
        or settings.appstore_private_key_path is None
    ):
        raise ConfigurationError(
            "the App Store Connect key id, issuer id and key path are required"
        )
    tokens = TokenProvider(
        key_id=settings.appstore_key_id,
        issuer_id=settings.appstore_issuer_id,
        private_key_pem=load_private_key(settings.appstore_private_key_path),
    )
    http = httpx.AsyncClient(timeout=settings.appstore_timeout_seconds)
    reads = AppStoreConnectGateway(AscClient(http, tokens))
    reports = UnsupportedReports("App Store Connect")
    return CompositeGateway(
        catalog=reads,
        builds=reads,
        testers=reads,
        reviews=reads,
        sales=reports,
        engagement=reports,
    )
