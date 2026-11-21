import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from asc_support import generate_key_pem, public_pem, token_provider, write_key_file
from release_assistant.config import ConfigurationError
from release_assistant.infrastructure.appstoreconnect.auth import TokenProvider, load_private_key

START = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


# --- the token ------------------------------------------------------------------------


def test_the_token_is_an_es256_jwt_with_the_claims_apple_expects() -> None:
    pem = generate_key_pem()
    provider = TokenProvider(
        key_id="KEY123", issuer_id="issuer-1", private_key_pem=pem, clock=Clock()
    )

    token = provider.token()

    header = jwt.get_unverified_header(token)
    claims = jwt.decode(
        token,
        public_pem(pem),
        algorithms=["ES256"],
        audience="appstoreconnect-v1",
        options={"verify_exp": False, "verify_iat": False},
    )
    assert (header["alg"], header["kid"], header["typ"]) == ("ES256", "KEY123", "JWT")
    assert claims["iss"] == "issuer-1"
    assert claims["aud"] == "appstoreconnect-v1"
    assert claims["exp"] - claims["iat"] == 600


def test_the_token_is_reused_and_renewed_before_it_expires() -> None:
    clock = Clock()
    provider = TokenProvider(
        key_id="K", issuer_id="i", private_key_pem=generate_key_pem(), clock=clock
    )
    first = provider.token()

    clock.now += timedelta(minutes=7)
    assert provider.token() == first

    clock.now += timedelta(minutes=1)  # inside the 2-minute renewal margin of a 10-minute token
    assert provider.token() != first


def test_a_lifetime_beyond_twenty_minutes_is_refused() -> None:
    with pytest.raises(ValueError, match="20 minutes"):
        TokenProvider(
            key_id="K",
            issuer_id="i",
            private_key_pem=generate_key_pem(),
            lifetime=timedelta(minutes=21),
        )


def test_a_wrong_key_does_not_verify_the_signature() -> None:
    token = token_provider().token()

    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(
            token,
            public_pem(generate_key_pem()),
            algorithms=["ES256"],
            audience="appstoreconnect-v1",
            options={"verify_exp": False, "verify_iat": False},
        )


# --- loading the key file --------------------------------------------------------------


def test_a_valid_owner_only_p256_key_loads(tmp_path: Path) -> None:
    pem = generate_key_pem()

    assert load_private_key(write_key_file(tmp_path, pem)) == pem


def test_a_missing_file_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="not found"):
        load_private_key(tmp_path / "nothing.p8")


def test_a_directory_is_not_a_key(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="not a file"):
        load_private_key(tmp_path)


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o666])
def test_a_key_readable_by_others_is_refused(tmp_path: Path, mode: int) -> None:
    path = write_key_file(tmp_path, generate_key_pem(), mode=mode)

    with pytest.raises(ConfigurationError, match="readable by other users"):
        load_private_key(path)


def test_owner_only_read_modes_are_accepted(tmp_path: Path) -> None:
    path = write_key_file(tmp_path, generate_key_pem(), mode=0o400)

    assert os.stat(path).st_mode & 0o077 == 0
    assert load_private_key(path)


@pytest.mark.parametrize(
    "content",
    ["not a key at all", "-----BEGIN PRIVATE KEY-----\nbm9wZQ==\n-----END PRIVATE KEY-----\n", ""],
)
def test_garbage_is_not_a_key_and_is_never_echoed(tmp_path: Path, content: str) -> None:
    path = write_key_file(tmp_path, content)

    with pytest.raises(ConfigurationError) as raised:
        load_private_key(path)

    assert "valid unencrypted PEM" in str(raised.value)
    assert "bm9wZQ" not in str(raised.value)


def test_keys_of_the_wrong_kind_are_refused(tmp_path: Path) -> None:
    from cryptography.hazmat.primitives import serialization

    wrong = {
        "rsa": rsa.generate_private_key(65537, 2048),
        "p384": ec.generate_private_key(ec.SECP384R1()),
    }
    for name, key in wrong.items():
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        path = tmp_path / f"{name}.p8"
        path.write_text(pem)
        os.chmod(path, 0o600)

        with pytest.raises(ConfigurationError, match="EC P-256"):
            load_private_key(path)
