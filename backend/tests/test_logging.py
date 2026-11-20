import io
import json
import logging
from collections.abc import Iterator

import pytest
import structlog

from release_assistant.application.risk import ConfirmationChannel
from release_assistant.bootstrap import build_mock_application
from release_assistant.config import Settings
from release_assistant.observability.logging import REDACTED, configure_logging


@pytest.fixture
def stream() -> Iterator[io.StringIO]:
    buffer = io.StringIO()
    yield buffer
    structlog.reset_defaults()
    logging.getLogger().handlers.clear()


def lines(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_json_output_has_event_level_and_utc_timestamp(stream: io.StringIO) -> None:
    configure_logging(Settings(log_json=True), stream=stream)

    structlog.get_logger("demo").info("thing.happened", review_id="review-1")

    [entry] = lines(stream)
    assert entry["event"] == "thing.happened"
    assert entry["level"] == "info"
    assert entry["review_id"] == "review-1"
    assert str(entry["timestamp"]).endswith("Z")


def test_sensitive_keys_are_redacted(stream: io.StringIO) -> None:
    configure_logging(Settings(log_json=True), stream=stream)

    structlog.get_logger().info("oops", email="anna@example.com", body="secret", action_id="a1")

    [entry] = lines(stream)
    assert entry["email"] == REDACTED
    assert entry["body"] == REDACTED
    assert entry["action_id"] == "a1"


def test_level_filters_lower_severity(stream: io.StringIO) -> None:
    configure_logging(Settings(log_json=True, log_level="WARNING"), stream=stream)

    structlog.get_logger().info("quiet")
    structlog.get_logger().warning("loud")

    assert [e["event"] for e in lines(stream)] == ["loud"]


def test_standard_library_logs_share_the_format(stream: io.StringIO) -> None:
    configure_logging(Settings(log_json=True), stream=stream)

    logging.getLogger("uvicorn.error").warning("from a third-party library")

    [entry] = lines(stream)
    assert entry["event"] == "from a third-party library"
    assert entry["logger"] == "uvicorn.error"


def test_exceptions_are_serialized_into_the_json_record(stream: io.StringIO) -> None:
    configure_logging(Settings(log_json=True), stream=stream)

    try:
        raise RuntimeError("boom")
    except RuntimeError:
        structlog.get_logger().exception("failed")

    [entry] = lines(stream)
    assert "RuntimeError: boom" in str(entry["exception"])


def test_console_format_is_not_json(stream: io.StringIO) -> None:
    configure_logging(Settings(log_json=False), stream=stream)

    structlog.get_logger().info("hello")

    assert "hello" in stream.getvalue()
    with pytest.raises(json.JSONDecodeError):
        json.loads(stream.getvalue())


async def test_action_audit_trail_has_ids_but_no_content(stream: io.StringIO) -> None:
    configure_logging(Settings(log_json=True), stream=stream)
    app = build_mock_application()
    session = app.start_session()

    pending = await session.propose(
        "add_tester", {"group_id": "group-1", "email": "private.person@example.com"}
    )
    await app.actions.confirm(pending.id, ConfirmationChannel.BUTTON)  # type: ignore[union-attr]

    events = [e["event"] for e in lines(stream)]
    assert events == ["action.proposed", "action.executed"]
    assert "private.person@example.com" not in stream.getvalue()
