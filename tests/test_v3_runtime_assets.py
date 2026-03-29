"""Regression checks for v3 runtime contracts and schema assets."""

from __future__ import annotations

from pathlib import Path

import utilities


EXPECTED_FUNCTION_TOOLS = {
    "load_case_context",
    "search_exemplars",
    "load_memory_summaries",
    "load_memory_entry",
    "save_memory_candidate",
    "archive_session",
    "enqueue_review",
    "report_ui_status",
}


def _repo_root() -> Path:
    """Return the repository root above `zb_app`."""

    return Path(__file__).resolve().parents[2]


def test_openai_function_tools_are_strict_and_complete():
    """The Responses tool catalog should expose the expected strict function tools."""

    tools = utilities.response_tool_definitions()
    function_tools = [tool for tool in tools if tool.get("type") == "function"]

    assert {tool["name"] for tool in function_tools} == EXPECTED_FUNCTION_TOOLS
    for tool in function_tools:
        assert tool["strict"] is True
        assert tool["parameters"]["type"] == "object"
        assert tool["parameters"]["additionalProperties"] is False


def test_action_schema_supports_needs_cm_review_filter():
    """The GPT-facing action schema should document the live review filter enum."""

    action_schema = (
        _repo_root() / "zenbot_knowledge" / "action_schemas.yaml"
    ).read_text(encoding="utf-8")

    assert "needs_cm_review" in action_schema
