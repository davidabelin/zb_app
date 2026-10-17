"""Regression checks for v3 runtime contracts and schema assets."""

from __future__ import annotations

import json
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


def test_response_tool_schema_exposes_paginated_memory_and_solution_options():
    """The tool schemas should expose the optional retrieval expansion fields."""

    tools = {
        tool["name"]: tool
        for tool in utilities.response_tool_definitions()
        if tool.get("type") == "function"
    }

    case_properties = tools["load_case_context"]["parameters"]["properties"]
    memory_properties = tools["load_memory_summaries"]["parameters"]["properties"]

    assert "include_solution_notes" in case_properties
    assert "end_index" in memory_properties


def test_load_case_context_can_include_project_solution_notes():
    """Case-context lookup should opt into known project solution notes."""

    result = utilities._dispatch_function_tool(
        "load_case_context",
        json.dumps({"case_id": "46", "include_solution_notes": True}),
    )

    payload = result["payload"]
    assert payload["status"] == "success"
    assert payload["case_id"] == "46"
    assert payload["solution_notes"] == ["Climb down."]


def test_load_memory_summaries_supports_end_index_pagination(monkeypatch):
    """The internal memory tool should match the API's backward pagination."""

    monkeypatch.setattr(
        utilities,
        "load_memory_logbook",
        lambda: [
            {"serial_number": "001", "title": "one"},
            {"serial_number": "002", "title": "two"},
            {"serial_number": "003", "title": "three"},
            {"serial_number": "004", "title": "four"},
        ],
    )

    result = utilities._dispatch_function_tool(
        "load_memory_summaries",
        json.dumps({"limit": 2, "end_index": 2}),
    )

    payload = result["payload"]
    assert payload["status"] == "success"
    assert [item["serial_number"] for item in payload["summaries"]] == ["003", "002"]
    assert payload["start_index"] == 1
    assert payload["end_index"] == 2
    assert payload["next_end_index"] == 0
    assert payload["has_more"] is True


def test_action_schema_supports_needs_cm_review_filter():
    """The GPT-facing action schema should document the live review filter enum."""

    action_schema = (
        _repo_root() / "zenbot_knowledge" / "action_schemas.yaml"
    ).read_text(encoding="utf-8")

    assert "needs_cm_review" in action_schema
