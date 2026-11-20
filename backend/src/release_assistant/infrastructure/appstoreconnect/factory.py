"""Building the real gateway from configuration, failing at startup if it cannot work."""

import httpx

from release_assistant.config import ConfigurationError, Settings
from release_assistant.domain.ports import AppStoreGateway, SalesReports
from release_assistant.infrastructure.appstoreconnect.auth import TokenProvider, load_private_key
from release_assistant.infrastructure.appstoreconnect.client import AscClient
from release_assistant.infrastructure.appstoreconnect.gateway import AppStoreConnectGateway
from release_assistant.infrastructure.appstoreconnect.sales import SalesReader
from release_assistant.infrastructure.composite_gateway import CompositeGateway, UnsupportedReports


def create_appstoreconnect_gateway(settings: Settings) -> AppStoreGateway:
    """The real service for what it supports (reading apps, builds, versions, TestFlight,
    reviews and, when a vendor number is set, sales and downloads); engagement analytics
    answer "not supported yet".

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
    client = AscClient(http, tokens)
    reads = AppStoreConnectGateway(client)
    sales: SalesReports = (
        SalesReader(client, settings.appstore_vendor_number)
        if settings.appstore_vendor_number
        else UnsupportedReports(
            "App Store Connect", what="sales (set RELEASE_ASSISTANT_APPSTORE_VENDOR_NUMBER)"
        )
    )
    return CompositeGateway(
        catalog=reads,
        builds=reads,
        testers=reads,
        reviews=reads,
        sales=sales,
        engagement=UnsupportedReports("App Store Connect", what="engagement analytics"),
    )
