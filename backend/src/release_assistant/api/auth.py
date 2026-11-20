"""API key authentication (ADR 0004). Keys are compared by hash, in constant time."""

import hashlib
import hmac
from collections.abc import Iterable
from dataclasses import dataclass

from release_assistant.config import ApiClient


@dataclass(frozen=True)
class Principal:
    """The authenticated caller and what it may do."""

    subject: str
    owner: str
    scopes: frozenset[str]


class Authenticator:
    """
    Args:
        clients: Configured credentials. With none, every token is rejected (fail closed).
    """

    def __init__(self, clients: Iterable[ApiClient]) -> None:
        self._clients = list(clients)

    def authenticate(self, token: str) -> Principal | None:
        digest = hashlib.sha256(token.encode()).hexdigest()
        for client in self._clients:
            if hmac.compare_digest(digest, client.key_sha256):
                return Principal(client.subject, client.owner, frozenset(client.scopes))
        return None
