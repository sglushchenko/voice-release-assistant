"""Defensive response headers for a JSON API."""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# The interactive docs load scripts and styles from a CDN, which a strict policy would block.
_DOCS_PATHS = frozenset({"/docs", "/docs/oauth2-redirect"})


class SecurityHeadersMiddleware:
    """Adds headers to every response, including errors and refusals produced by other
    middleware. Existing values are never overwritten, so a route can opt out deliberately.

    - `X-Content-Type-Options: nosniff` stops browsers guessing a type for a JSON body.
    - `Cache-Control: no-store` keeps tokens and account data out of shared caches.
    - `Content-Security-Policy` and `X-Frame-Options` make any rendered response inert.
    - `Referrer-Policy: no-referrer` stops URLs, which carry ids, leaking onward.
    - `Strict-Transport-Security` only on HTTPS requests; behind a TLS-terminating proxy
      that is what Uvicorn reports when the proxy is trusted.

    Args:
        app: The wrapped ASGI application.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        is_docs = scope["path"] in _DOCS_PATHS
        is_https = scope.get("scheme") == "https"

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("Cache-Control", "no-store")
                headers.setdefault("Referrer-Policy", "no-referrer")
                headers.setdefault("X-Frame-Options", "DENY")
                if not is_docs:
                    headers.setdefault(
                        "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"
                    )
                if is_https:
                    headers.setdefault(
                        "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
                    )
            await send(message)

        await self.app(scope, receive, send_with_headers)
