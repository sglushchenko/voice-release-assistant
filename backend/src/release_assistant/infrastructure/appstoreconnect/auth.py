"""Signing the short-lived tokens App Store Connect asks for.

Every request carries a JWT signed (ES256) with the team's API private key. The key is read
once at startup, from a path outside the repository; its contents are never logged, never put in
an error message and never leave this module except as a signature.
"""

import stat
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from release_assistant.config import ConfigurationError

# Apple rejects tokens that live longer than 20 minutes.
MAX_LIFETIME = timedelta(minutes=20)
_AUDIENCE = "appstoreconnect-v1"


def load_private_key(path: Path) -> str:
    """Read and validate the `.p8` key file, failing with a message that names the problem
    but never the content. Called at startup so a bad key stops the server immediately."""
    try:
        info = path.stat()
    except OSError:
        raise ConfigurationError(f"App Store Connect key file not found at {path}") from None
    if not stat.S_ISREG(info.st_mode):
        raise ConfigurationError(f"{path} is not a file")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise ConfigurationError(
            f"{path} is readable by other users; restrict it first (chmod 600 {path.name})"
        )
    try:
        pem = path.read_bytes()
        key = serialization.load_pem_private_key(pem, password=None)
    except (OSError, ValueError, TypeError):
        raise ConfigurationError(f"{path} is not a valid unencrypted PEM private key") from None
    if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
        raise ConfigurationError(f"{path} is not an EC P-256 key, which App Store Connect uses")
    return pem.decode()


class TokenProvider:
    """Hands out a valid token, signing a new one shortly before the old one expires.

    Args:
        key_id: The API key's Key ID (the `kid` header).
        issuer_id: The team's Issuer ID (the `iss` claim).
        private_key_pem: The key as PEM text, from `load_private_key`.
        lifetime: How long each token is valid; at most 20 minutes.
        clock: Injected so tests control time.
    """

    def __init__(
        self,
        *,
        key_id: str,
        issuer_id: str,
        private_key_pem: str,
        lifetime: timedelta = timedelta(minutes=10),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if lifetime > MAX_LIFETIME:
            raise ValueError("App Store Connect rejects tokens that live longer than 20 minutes")
        self._key_id = key_id
        self._issuer_id = issuer_id
        self._key = private_key_pem
        self._lifetime = lifetime
        self._clock = clock or (lambda: datetime.now(UTC))
        self._token: str | None = None
        self._expires: datetime | None = None

    def token(self) -> str:
        now = self._clock()
        # Renewed with a margin so a token never expires while a request is in flight.
        if (
            self._token is None
            or self._expires is None
            or now >= self._expires - timedelta(minutes=2)
        ):
            self._expires = now + self._lifetime
            self._token = jwt.encode(
                {
                    "iss": self._issuer_id,
                    "iat": int(now.timestamp()),
                    "exp": int(self._expires.timestamp()),
                    "aud": _AUDIENCE,
                },
                self._key,
                algorithm="ES256",
                headers={"kid": self._key_id, "typ": "JWT"},
            )
        return self._token
