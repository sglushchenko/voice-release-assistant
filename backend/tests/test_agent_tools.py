"""The tool catalogue: its shape, its safety properties and its agreement with the API."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from release_assistant.agent.tools import DEFAULT_TOOLS, AppArgs
from release_assistant.application.reviews import RESPOND_TO_REVIEW
from release_assistant.application.testers import ADD_TESTER, REMOVE_TESTER

CONTRACT = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"


def test_names_are_unique() -> None:
    names = [tool.name for tool in DEFAULT_TOOLS]

    assert len(names) == len(set(names))


def test_the_agent_has_no_way_to_confirm_anything() -> None:
    for tool in DEFAULT_TOOLS:
        assert "confirm" not in tool.name
        # Confirming and rejecting live under /v1/actions; the agent only proposes, through
        # the session's own actions route.
        assert not tool.path.startswith("/v1/actions"), tool.name
        assert not tool.path.endswith(("/confirm", "/reject")), tool.name


def test_every_write_is_a_proposal_and_every_proposal_names_a_real_action() -> None:
    writes = [tool for tool in DEFAULT_TOOLS if tool.method == "POST"]

    assert writes
    for tool in writes:
        assert tool.name.startswith("propose_")
        assert tool.action in {ADD_TESTER, REMOVE_TESTER, RESPOND_TO_REVIEW}
        assert tool.path == "/v1/sessions/{session_id}/actions"
        assert "PROPOSES" in tool.description
    assert all(tool.action is None for tool in DEFAULT_TOOLS if tool.method == "GET")


def test_review_tools_warn_the_model_about_untrusted_text() -> None:
    review_tools = [t for t in DEFAULT_TOOLS if "reviews" in t.path]

    assert {t.name for t in review_tools} == {"read_reviews", "unanswered_reviews"}
    assert all("untrusted" in t.description for t in review_tools)


def test_schemas_are_strict_objects_a_provider_can_use() -> None:
    for tool in DEFAULT_TOOLS:
        schema = tool.spec.parameters
        assert schema["type"] == "object", tool.name
        assert schema["additionalProperties"] is False, tool.name
        assert tool.description.strip(), tool.name


def test_every_tool_route_exists_in_the_committed_api_contract() -> None:
    paths = json.loads(CONTRACT.read_text(encoding="utf-8"))["paths"]

    for tool in DEFAULT_TOOLS:
        assert tool.path in paths, f"{tool.name}: {tool.path} is not in docs/openapi.json"
        assert tool.method.lower() in paths[tool.path], f"{tool.name}: {tool.method} {tool.path}"


def test_query_parameters_are_declared_by_the_api() -> None:
    paths = json.loads(CONTRACT.read_text(encoding="utf-8"))["paths"]

    for tool in DEFAULT_TOOLS:
        declared = {
            p["name"]
            for p in paths[tool.path][tool.method.lower()].get("parameters", [])
            if p["in"] == "query"
        }
        assert set(tool.query) <= declared, f"{tool.name}: {set(tool.query) - declared}"
        assert set(tool.query) <= set(tool.arguments.model_fields), tool.name


@pytest.mark.parametrize(
    "unsafe",
    ["../etc/passwd", "a/b", "a b", "%2e%2e", "app-1/../x", "", "x" * 65, "a?b=c", "a#b", "a\nb"],
)
def test_ids_that_end_up_in_a_url_path_cannot_carry_path_tricks(unsafe: str) -> None:
    with pytest.raises(ValidationError):
        AppArgs(app_id=unsafe)


@pytest.mark.parametrize("safe", ["app-1", "group_2", "A1b2", "x" * 64])
def test_ordinary_ids_are_accepted(safe: str) -> None:
    assert AppArgs(app_id=safe).app_id == safe


def test_unknown_arguments_are_refused() -> None:
    with pytest.raises(ValidationError):
        AppArgs(app_id="app-1", sneaky="1")  # type: ignore[call-arg]
