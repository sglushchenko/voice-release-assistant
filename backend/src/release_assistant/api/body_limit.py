"""Reject oversized request bodies before they are read into memory (ADR 0006)."""

import contextlib

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from release_assistant.api.errors import problem


class _BodyTooLarge(Exception):
    pass


class BodyLimitMiddleware:
    """Pure ASGI middleware, so it can watch the body as it streams in.

    A declared Content-Length over the limit is refused immediately. A body that lies about
    its length, or is streamed without one, is cut off once it exceeds the limit.

    Args:
        app: The wrapped ASGI application.
        max_bytes: Largest body accepted.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = dict(scope["headers"]).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > self._max_bytes:
            await self._refuse(scope, receive, send)
            return

        received = 0
        response_started = False
        refused = False

        async def limited_receive() -> Message:
            nonlocal received, refused
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self._max_bytes:
                    # FastAPI turns any error raised while reading a body into a 400, so the
                    # 413 is sent here, at the moment the limit is crossed, and whatever the
                    # application tries to answer afterwards is dropped.
                    if not response_started:
                        refused = True
                        await self._refuse(scope, receive, send)
                    raise _BodyTooLarge
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if refused:
                return
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        with contextlib.suppress(_BodyTooLarge):
            await self.app(scope, limited_receive, tracking_send)

    async def _refuse(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = problem(
            413, "payload_too_large", f"Request body exceeds {self._max_bytes} bytes"
        )
        await response(scope, receive, send)
