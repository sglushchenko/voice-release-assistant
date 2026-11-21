"""Talk to the agent from a terminal: `uv run release-assistant-chat`.

The fastest way to see how the agent behaves with real tools and real model wording, before
any app exists. It runs the actual HTTP API in process, so the agent goes through the same
authentication, scopes and confirmation rules as a production client: the agent's credential
can read and propose, and only the person at the keyboard (`/confirm`) can approve a change.
"""

import argparse
import asyncio
import hashlib
import secrets
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from release_assistant.agent.chat import Chat
from release_assistant.agent.conversation import InMemoryConversationStore
from release_assistant.agent.errors import LLMError, ToolBackendError
from release_assistant.agent.http_backend import HttpToolBackend
from release_assistant.agent.messages import AgentRun, Role, StopReason
from release_assistant.agent.openai_model import create_openai_model
from release_assistant.agent.ports import LLMClient
from release_assistant.agent.runner import Agent
from release_assistant.api.app import create_app
from release_assistant.config import ApiClient, ConfigurationError, Settings
from release_assistant.observability.logging import configure_logging

HELP = """\
Type a message to talk to the assistant. Commands:
  /pending          changes the assistant proposed that wait for you
  /confirm <id>     approve one (only you can; the assistant cannot)
  /reject <id>      cancel one
  /trace            what the assistant did for your last message: tools, arguments, results
  /reset            forget the conversation and start again
  /help, /quit"""

_OWNER = "you"
_PREVIEW_CHARS = 160


@dataclass(frozen=True)
class Reply:
    """What to print after a line of input, and whether the session continues."""

    text: str
    quit: bool = False


class Console:
    """One person's terminal conversation.

    Args:
        model: The language model the agent uses.
        agent_client: HTTP client holding the agent's credential (read, propose).
        person_client: HTTP client holding yours (also confirms). Kept apart from the agent's
            on purpose: it is never given to the agent or the model.
    """

    def __init__(
        self, model: LLMClient, agent_client: httpx.AsyncClient, person_client: httpx.AsyncClient
    ) -> None:
        self._model = model
        self._agent_client = agent_client
        self._person = person_client
        self._chat: Chat | None = None
        self._last_run: AgentRun | None = None

    async def handle(self, line: str) -> Reply:
        line = line.strip()
        if not line:
            return Reply("")
        if not line.startswith("/"):
            return await self._say(line)
        command, _, argument = line.partition(" ")
        match command:
            case "/quit" | "/exit":
                return Reply("", quit=True)
            case "/help":
                return Reply(HELP)
            case "/reset":
                self._chat = None
                self._last_run = None
                return Reply("Conversation cleared.")
            case "/pending":
                return Reply(await self._pending())
            case "/confirm":
                return Reply(await self._decide(argument.strip(), "confirm"))
            case "/reject":
                return Reply(await self._decide(argument.strip(), "reject"))
            case "/trace":
                return Reply(self._trace())
            case _:
                return Reply(f"Unknown command {command}. /help lists them.")

    async def _say(self, text: str) -> Reply:
        try:
            if self._chat is None:
                backend = await HttpToolBackend.open(self._agent_client)
                self._chat = Chat(
                    Agent(self._model, backend), InMemoryConversationStore(), uuid.uuid4().hex
                )
            run = await self._chat.send(text)
        except ToolBackendError as error:
            return Reply(f"Could not reach the tools: {error}")
        self._last_run = run
        lines = [run.answer or "(no answer)"]
        for action_id in run.pending_action_ids:
            lines.append(await self._describe(action_id))
        lines.append(_footer(run))
        return Reply("\n".join(lines))

    async def _describe(self, action_id: str) -> str:
        response = await self._person.get(f"/v1/actions/{action_id}")
        if response.status_code != 200:
            return f"  waiting for you: {action_id}"
        action = response.json()
        return (
            f"  waiting for you: {action['summary']}\n"
            f"  /confirm {action_id}   or   /reject {action_id}"
        )

    async def _pending(self) -> str:
        response = await self._person.get("/v1/actions", params={"status": "pending"})
        if response.status_code != 200:
            return _problem(response)
        actions: list[dict[str, Any]] = response.json()
        if not actions:
            return "Nothing is waiting for you."
        return "\n".join(f"{a['id']}  {a['summary']}" for a in actions)

    async def _decide(self, action_id: str, verb: str) -> str:
        if not action_id:
            return f"Usage: /{verb} <id>. /pending lists the ids."
        # Typing the command yourself is the confirmation, the terminal's counterpart of a button.
        body = {"channel": "button"} if verb == "confirm" else None
        response = await self._person.post(f"/v1/actions/{action_id}/{verb}", json=body)
        if response.status_code != 200:
            return _problem(response)
        return "Done." if verb == "confirm" else "Cancelled."

    def _trace(self) -> str:
        if self._last_run is None:
            return "Nothing to show yet."
        lines: list[str] = []
        for message in self._last_run.new_messages:
            if message.role is Role.ASSISTANT:
                lines.extend(f"-> {c.name}({c.arguments})" for c in message.tool_calls)
            elif message.role is Role.TOOL and message.tool_result is not None:
                result = message.tool_result
                kind = "error" if result.is_error else result.trust.value
                preview = result.content.replace("\n", " ")[:_PREVIEW_CHARS]
                lines.append(f"<- {result.name} [{kind}] {preview}")
        return "\n".join(lines) or "The assistant answered without using any tool."


def _footer(run: AgentRun) -> str:
    note = f"[steps {run.steps} · tools {run.tool_calls_made} · tokens {run.usage.total}"
    if run.stop_reason is not StopReason.ANSWERED:
        note += f" · stopped: {run.stop_reason.value}"
    return note + "]"


def _problem(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail") or response.json().get("title")
    except ValueError:
        detail = None
    return f"Refused ({response.status_code}): {detail or 'no details'}"


def open_console(settings: Settings, model: LLMClient) -> Console:
    """The API in process over `settings.backend`, plus the two credentials that go with it.

    The keys are random per run and exist only in this process.
    """
    agent_key, person_key = secrets.token_urlsafe(24), secrets.token_urlsafe(24)

    def client(subject: str, key: str, scopes: set[str]) -> ApiClient:
        return ApiClient(
            subject=subject,
            owner=_OWNER,
            key_sha256=hashlib.sha256(key.encode()).hexdigest(),
            scopes=frozenset(scopes),
        )

    api_settings = settings.model_copy(
        update={
            "api_clients": [
                client("chat-agent", agent_key, {"read", "propose"}),
                client("chat-person", person_key, {"read", "propose", "confirm:button"}),
            ],
            "admin_username": None,
            "admin_password_hash": None,
            "rate_limit_enabled": False,
        }
    )
    app = create_app(api_settings)

    def http(key: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://chat",
            headers={"Authorization": f"Bearer {key}"},
            timeout=60,
        )

    return Console(model, http(agent_key), http(person_key))


async def _repl(console: Console, first: str | None) -> None:
    if first is not None:
        print((await console.handle(first)).text)
        return
    print("Release assistant. /help for commands, /quit to leave.")
    while True:
        try:
            line = await asyncio.to_thread(input, "you> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return
        reply = await console.handle(line)
        if reply.text:
            print(reply.text)
        if reply.quit:
            return


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="release-assistant-chat", description=__doc__)
    parser.add_argument(
        "--backend", choices=["mock", "appstoreconnect"], help="override the .env choice"
    )
    parser.add_argument("--ask", metavar="TEXT", help="send one message, print the answer, exit")
    parser.add_argument("--verbose", action="store_true", help="show the application's logs")
    args = parser.parse_args(argv)

    try:
        settings = Settings(backend=args.backend) if args.backend else Settings()
        configure_logging(
            settings.model_copy(
                update={"log_level": "INFO" if args.verbose else "ERROR", "log_json": False}
            )
        )
        model = create_openai_model(settings)
        console = open_console(settings, model)
    except (LLMError, ConfigurationError, ValueError) as error:
        print(f"Cannot start: {error}", file=sys.stderr)
        return 2

    if settings.backend != "mock":
        print(
            "Real App Store Connect data (read-only) will be sent to the language model provider.",
            file=sys.stderr,
        )
    asyncio.run(_repl(console, args.ask))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
