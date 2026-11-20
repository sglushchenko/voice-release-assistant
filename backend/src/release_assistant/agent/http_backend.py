"""Runs tool calls against the assistant's HTTP API with the agent's own credential."""

import json
from collections.abc import Sequence
from typing import Any, Self
from urllib.parse import quote

import httpx
import structlog
from pydantic import ValidationError

from release_assistant.agent.errors import ToolBackendError
from release_assistant.agent.messages import ToolCall, ToolResult, Trust
from release_assistant.agent.tools import DEFAULT_TOOLS, Tool

log = structlog.get_logger(__name__)

_MAX_RESULT_CHARS = 20_000


class HttpToolBackend:
    """A ToolBackend that speaks to the API.

    The client carries the agent's bearer key (read and propose scopes only), so the server
    enforces what the agent may do regardless of what this code or the model attempts.

    Args:
        client: HTTP client with the API base URL, the agent's `Authorization` header and a
            sensible timeout.
        session_id: The API session this conversation belongs to; reading reviews in it is
            what marks the session untrusted, server-side.
        tools: The tools on offer.
        max_result_chars: Longer responses are cut so one call cannot flood the model's context.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        session_id: str,
        tools: Sequence[Tool] = DEFAULT_TOOLS,
        *,
        max_result_chars: int = _MAX_RESULT_CHARS,
    ) -> None:
        self._client = client
        self._session_id = session_id
        self._tools = {tool.name: tool for tool in tools}
        self._max_result_chars = max_result_chars

    @property
    def session_id(self) -> str:
        return self._session_id

    @classmethod
    async def open(cls, client: httpx.AsyncClient, tools: Sequence[Tool] = DEFAULT_TOOLS) -> Self:
        """Start an API session for a new conversation and return a backend bound to it."""
        try:
            response = await client.post("/v1/sessions")
            response.raise_for_status()
            session_id = str(response.json()["session_id"])
        except (httpx.HTTPError, KeyError, ValueError) as error:
            raise ToolBackendError("could not open an API session") from error
        return cls(client, session_id, tools)

    async def call(self, call: ToolCall) -> ToolResult:
        tool = self._tools.get(call.name)
        if tool is None:
            return self._error(call, f"Unknown tool {call.name!r}. Use only the tools offered.")
        try:
            arguments = tool.arguments.model_validate(call.arguments)
        except ValidationError as error:
            # Location and message only: the offending values are never echoed back.
            detail = "; ".join(
                f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors()
            )
            return self._error(call, f"Invalid arguments: {detail}")

        values = arguments.model_dump(mode="json", exclude_none=True)
        try:
            response = await self._client.request(
                tool.method,
                self._path(tool, values),
                params={k: v for k, v in values.items() if k in tool.query} or None,
                json=self._body(tool, values),
            )
        except httpx.HTTPError:
            log.warning("agent.tool_unreachable", tool=tool.name)
            return self._error(call, "The service is unavailable right now. Try again later.")

        return self._result(call, tool, response)

    def _path(self, tool: Tool, values: dict[str, Any]) -> str:
        parts = {"session_id": self._session_id, **values}
        # Quoting is a second line of defence: the argument models already restrict ids.
        return tool.path.format_map({k: quote(str(v), safe="") for k, v in parts.items()})

    @staticmethod
    def _body(tool: Tool, values: dict[str, Any]) -> dict[str, Any] | None:
        if tool.action is None:
            return None
        return {"action": tool.action, "arguments": values}

    def _result(self, call: ToolCall, tool: Tool, response: httpx.Response) -> ToolResult:
        text = response.text
        if len(text) > self._max_result_chars:
            omitted = len(text) - self._max_result_chars
            text = f"{text[: self._max_result_chars]}\n[truncated: {omitted} characters omitted]"
        payload = _json_object(response)
        if response.is_error:
            return ToolResult(call_id=call.id, name=call.name, content=text, is_error=True)

        # The server labels third-party text; the label, not the tool name, decides trust.
        trust = Trust.UNTRUSTED if payload.get("trust") == "untrusted" else Trust.TRUSTED
        pending_id: str | None = None
        if tool.action is not None and payload.get("status") == "pending":
            action = payload.get("action")
            if isinstance(action, dict):
                pending_id = str(action.get("id"))
        return ToolResult(
            call_id=call.id,
            name=call.name,
            content=text,
            trust=trust,
            pending_action_id=pending_id,
        )

    @staticmethod
    def _error(call: ToolCall, message: str) -> ToolResult:
        content = json.dumps({"error": message})
        return ToolResult(call_id=call.id, name=call.name, content=content, is_error=True)


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}
