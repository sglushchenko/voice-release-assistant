"""The HTTP client for App Store Connect: tokens, retries, pagination, errors."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any, NoReturn
from urllib.parse import urlsplit

import httpx
import structlog

from release_assistant.domain.errors import NotFoundError, UpstreamError
from release_assistant.infrastructure.appstoreconnect.auth import TokenProvider

log = structlog.get_logger(__name__)

BASE_URL = "https://api.appstoreconnect.apple.com"

_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_MAX_BACKOFF_SECONDS = 8.0
_LOW_QUOTA = 25


class AscClient:
    """Reads from the API. Failures become domain errors that are safe to show.

    Args:
        http: The HTTP client (carries the timeout). It must not send an Authorization header
            by itself: this class adds the token to every request.
        tokens: Source of the signed bearer token.
        max_pages: Pages followed for a list. A list longer than this is cut and a warning is
            logged, which bounds both memory and the use of the hourly request quota.
        max_retries: Extra attempts after a 429 or a server error.
        sleep: Injected so tests do not wait.
    """

    def __init__(
        self,
        http: httpx.AsyncClient,
        tokens: TokenProvider,
        *,
        max_pages: int = 5,
        max_retries: int = 3,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._http = http
        self._tokens = tokens
        self._max_pages = max_pages
        self._max_retries = max_retries
        self._sleep = sleep

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """One document, for example `/v1/apps/123`."""
        return await self._request(f"{BASE_URL}{path}", params)

    async def get_all(
        self, path: str, params: dict[str, Any] | None = None
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Every resource of a list (up to `max_pages` pages) and the `included` resources."""
        data: list[dict[str, Any]] = []
        included: list[dict[str, Any]] = []
        url = f"{BASE_URL}{path}"
        query = params
        for page in range(1, self._max_pages + 1):
            document = await self._request(url, query)
            data.extend(document.get("data", []))
            included.extend(document.get("included", []))
            next_url = document.get("links", {}).get("next")
            if next_url is None:
                return data, included
            if urlsplit(next_url).netloc != urlsplit(BASE_URL).netloc:
                # The bearer token must never be sent anywhere but Apple.
                raise UpstreamError("App Store Connect returned a link to another host")
            if page == self._max_pages:
                log.warning("asc.list_truncated", path=path, pages=page)
            url, query = next_url, None  # the next link already carries the query
        return data, included

    async def get_bytes(self, path: str, params: dict[str, Any] | None = None) -> bytes:
        """A binary document (reports are gzip files, not JSON). Same retries and error
        handling as `get`."""
        response = await self._send(f"{BASE_URL}{path}", params)
        if not response.is_success:
            self._raise_for(response)
        return response.content

    async def _request(self, url: str, params: dict[str, Any] | None) -> dict[str, Any]:
        return self._parse(await self._send(url, params))

    async def _send(self, url: str, params: dict[str, Any] | None) -> httpx.Response:
        for attempt in range(self._max_retries + 1):
            started = time.perf_counter()
            try:
                response = await self._http.get(
                    url,
                    params=params,
                    headers={"Authorization": f"Bearer {self._tokens.token()}"},
                )
            except httpx.HTTPError:
                log.warning("asc.unreachable", attempt=attempt)
                if attempt == self._max_retries:
                    raise UpstreamError("App Store Connect is unreachable right now") from None
                await self._sleep(self._backoff(attempt, None))
                continue

            self._note_quota(response)
            log.info(
                "asc.request",
                path=urlsplit(url).path,
                status=response.status_code,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
            )
            if response.status_code in _RETRY_STATUSES and attempt < self._max_retries:
                await self._sleep(self._backoff(attempt, response.headers.get("Retry-After")))
                continue
            return response
        raise UpstreamError("App Store Connect did not answer")  # pragma: no cover

    @staticmethod
    def _backoff(attempt: int, retry_after: str | None) -> float:
        if retry_after is not None and retry_after.isdigit():
            return min(float(retry_after), 60.0)
        return min(0.5 * float(2**attempt), _MAX_BACKOFF_SECONDS)

    @staticmethod
    def _note_quota(response: httpx.Response) -> None:
        """Apple reports the hourly quota as `X-Rate-Limit: user-hour-lim:N;user-hour-rem:M;`."""
        header = response.headers.get("X-Rate-Limit", "")
        fields = dict(part.split(":", 1) for part in header.split(";") if ":" in part)
        remaining = fields.get("user-hour-rem", "")
        if remaining.isdigit() and int(remaining) < _LOW_QUOTA:
            log.warning("asc.quota_low", remaining=int(remaining))

    @classmethod
    def _parse(cls, response: httpx.Response) -> dict[str, Any]:
        if not response.is_success:
            cls._raise_for(response)
        try:
            document = response.json()
        except ValueError:
            raise UpstreamError("App Store Connect returned an unreadable response") from None
        if not isinstance(document, dict):
            raise UpstreamError("App Store Connect returned an unexpected response")
        return document

    @staticmethod
    def _raise_for(response: httpx.Response) -> NoReturn:
        status = response.status_code
        code, title = _first_error(response)
        log.warning("asc.error", status=status, code=code)
        if status == 404:
            raise NotFoundError("App Store Connect has no such resource")
        if status == 401:
            raise UpstreamError(
                "App Store Connect rejected the credentials (401): check the key id, issuer id"
                " and key file"
            )
        if status == 403:
            raise UpstreamError(
                "App Store Connect denied this request (403): the API key's role does not allow it"
            )
        if status == 429:
            raise UpstreamError("App Store Connect rate limit reached; try again later")
        raise UpstreamError(f"App Store Connect error {status}: {title or code or 'unknown'}")


def _first_error(response: httpx.Response) -> tuple[str, str]:
    try:
        error = response.json()["errors"][0]
        return str(error.get("code", "")), str(error.get("title", ""))
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        return "", ""
