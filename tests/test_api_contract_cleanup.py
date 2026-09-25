from __future__ import annotations

import json
import re
from pathlib import Path

import yaml  # type: ignore[import]

import main
import utilities


def _schema() -> dict:
    schema_path = (
        Path(__file__).resolve().parents[2] / "zenbot_knowledge" / "action_schemas.yaml"
    )
    return yaml.safe_load(schema_path.read_text(encoding="utf-8"))


def _schema_json() -> dict:
    schema_path = (
        Path(__file__).resolve().parents[2] / "zenbot_knowledge" / "action_schemas.json"
    )
    return json.loads(schema_path.read_text(encoding="utf-8"))


def _normalized_api_rule_paths() -> set[str]:
    paths: set[str] = set()
    for rule in main.app.url_map.iter_rules():
        path = rule.rule
        if not path.startswith("/zb_api/"):
            continue
        normalized = re.sub(r"<(?:[^:>]+:)?([^>]+)>", r"{\1}", path)
        paths.add(normalized)
    return paths


def test_zb_api_blueprint_preserves_route_paths_and_methods():
    """Blueprint registration must not change the GPT-facing API contract."""
    api_rules = {
        rule.rule: (rule.endpoint, rule.methods)
        for rule in main.app.url_map.iter_rules()
        if rule.rule.startswith("/zb_api/")
    }

    assert api_rules["/zb_api/chat"][0] == "zb_api.zb_api_chat"
    assert api_rules["/zb_api/chat"][1] >= {"POST"}
    assert api_rules["/zb_api/chat/options"][1] >= {"GET"}
    assert api_rules["/zb_api/session-evaluations/summary"][1] >= {"GET"}
    assert api_rules["/zb_api/runtime/status-events"][1] >= {"POST"}


def test_api_auth_failures_return_json(monkeypatch):
    monkeypatch.setattr(main.utipy.config, "ZB_API_STRICT_AUTH", True)
    monkeypatch.setattr(main.utipy.config, "ACTION_API_TOKEN", "secret-token")

    client = main.app.test_client()
    response = client.get("/zb_api/chat/options")

    payload = response.get_json()
    assert response.status_code == 401
    assert response.mimetype == "application/json"
    assert payload["status"] == "failure"
    assert payload["error"] == "unauthorized"


def test_zb_api_chat_reports_created_and_continued_sessions(monkeypatch):
    monkeypatch.setattr(main.utipy.config, "ZB_API_STRICT_AUTH", False)
    monkeypatch.setattr(main.utipy, "DB", None)
    monkeypatch.setattr(main.utipy, "_LOCAL_CONVERSATIONS", {})
    monkeypatch.setattr(main.utipy, "get_model_reply", lambda *args, **kwargs: "ok")

    client = main.app.test_client()
    created = client.post("/zb_api/chat", json={"message": "first"})
    created_payload = created.get_json()

    continued = client.post(
        "/zb_api/chat",
        json={
            "message": "second",
            "conversation_id": created_payload["conversation_id"],
        },
    )
    continued_payload = continued.get_json()

    assert created.status_code == 200
    assert created_payload["status"] == "success"
    assert created_payload["conversation_status"] == "created"
    assert continued.status_code == 200
    assert continued_payload["conversation_status"] == "continued"
    assert continued_payload["conversation_id"] == created_payload["conversation_id"]


def test_zb_api_chat_recreates_unknown_conversation_id(monkeypatch):
    monkeypatch.setattr(main.utipy.config, "ZB_API_STRICT_AUTH", False)
    monkeypatch.setattr(main.utipy, "DB", None)
    monkeypatch.setattr(main.utipy, "_LOCAL_CONVERSATIONS", {})
    monkeypatch.setattr(main.utipy, "get_model_reply", lambda *args, **kwargs: "ok")

    client = main.app.test_client()
    requested_id = "bogus-cid-123"
    response = client.post(
        "/zb_api/chat",
        json={"message": "hello", "conversation_id": requested_id},
    )

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["conversation_status"] == "recreated_from_unknown_id"
    assert payload["requested_conversation_id"] == requested_id
    assert payload["conversation_id"] != requested_id
    assert requested_id not in main.utipy._LOCAL_CONVERSATIONS
    assert payload["conversation_id"] in main.utipy._LOCAL_CONVERSATIONS


def test_zb_api_chat_settings_lock_uses_standardized_failure(monkeypatch):
    monkeypatch.setattr(main.utipy.config, "ZB_API_STRICT_AUTH", False)
    monkeypatch.setattr(main.utipy, "DB", None)
    monkeypatch.setattr(main.utipy, "_LOCAL_CONVERSATIONS", {})
    conversation_id, _messages, _metadata = main.utipy.create_conversation(
        student="api-lock",
        settings={"preset_id": "balanced"},
    )

    client = main.app.test_client()
    response = client.post(
        "/zb_api/chat",
        json={
            "conversation_id": conversation_id,
            "message": "hello",
            "settings": {"preset_id": "fierce_barrier"},
        },
    )

    payload = response.get_json()
    assert response.status_code == 409
    assert payload["status"] == "failure"
    assert payload["error"] == "settings_locked"
    assert payload["conversation_id"] == conversation_id
    assert payload["session_settings"]["preset_id"] == "balanced"


def test_zb_api_save_chat_returns_normalized_success(monkeypatch, fake_bucket):
    monkeypatch.setattr(main.utipy.config, "ZB_API_STRICT_AUTH", False)
    monkeypatch.setattr(main.utipy, "BUCKET", fake_bucket)
    monkeypatch.setattr(utilities, "BUCKET", fake_bucket)
    monkeypatch.setattr(
        main.utipy,
        "get_conversation_state",
        lambda conversation_id: (
            [
                {"role": "system", "content": "You are Zenbot."},
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "world"},
            ],
            {
                "student": "api",
                "case_id": "",
                "model_name": utilities.config.MODEL_NAME,
                "profile": "live",
                "training_loss": 0.0,
                "botling_id": "balanced_mumon",
                "settings_version": "v3.1",
                "session_settings": utilities.resolve_session_settings(None),
            },
        ),
    )
    monkeypatch.setattr(
        main.utipy, "delete_messages_from_firestore", lambda conversation_id: None
    )
    monkeypatch.setattr(
        main.utipy,
        "submit_background_session_critic",
        lambda *args, **kwargs: "resp_123",
    )

    client = main.app.test_client()
    response = client.post("/zb_api/save_chat", json={"conversation_id": "save-123"})

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["status"] == "success"
    assert payload["conversation_id"] == "save-123"
    assert payload["archived"] is True
    assert payload["background_critic_response_id"] == "resp_123"
    assert "error" not in payload


def test_archive_and_review_routes_return_503_when_storage_unavailable(monkeypatch):
    monkeypatch.setattr(main.utipy.config, "ZB_API_STRICT_AUTH", False)
    monkeypatch.setattr(main.utipy.config, "LOCAL", True)
    monkeypatch.setattr(main.utipy.config, "ACTION_API_TOKEN", "")
    monkeypatch.setattr(main.utipy, "BUCKET", None)
    monkeypatch.setattr(utilities, "BUCKET", None)

    client = main.app.test_client()
    archive_response = client.get("/zb_api/conversations/list")
    review_response = client.get("/zb_api/session-evaluations/summary")
    save_response = client.post(
        "/zb_api/save_chat", json={"conversation_id": "missing"}
    )

    for response in (archive_response, review_response, save_response):
        payload = response.get_json()
        assert response.status_code == 503
        assert payload["status"] == "failure"
        assert payload["error"] == "storage_unavailable"


def test_deprecated_memory_routes_are_removed(monkeypatch):
    monkeypatch.setattr(main.utipy.config, "ZB_API_STRICT_AUTH", False)
    client = main.app.test_client()

    assert client.get("/zb_api/load_memory_logbook_full").status_code == 404
    assert client.post("/appendMemoryLogbookEntry", json={}).status_code == 404


def test_standardized_not_found_for_conversation_and_memory_entry(
    monkeypatch, fake_bucket
):
    monkeypatch.setattr(main.utipy.config, "ZB_API_STRICT_AUTH", False)
    monkeypatch.setattr(main.utipy, "BUCKET", fake_bucket)
    monkeypatch.setattr(utilities, "BUCKET", fake_bucket)
    monkeypatch.setattr(
        main.utipy, "get_memory_logbook_entry", lambda serial_number: None
    )

    client = main.app.test_client()
    conversation_response = client.get("/zb_api/conversations/missing-one")
    memory_response = client.get("/zb_api/load_memory_entry/999")

    conversation_payload = conversation_response.get_json()
    memory_payload = memory_response.get_json()

    assert conversation_response.status_code == 404
    assert conversation_payload["status"] == "failure"
    assert conversation_payload["error"] == "conversation_not_found"
    assert conversation_payload["conversation_id"] == "missing-one"

    assert memory_response.status_code == 404
    assert memory_payload["status"] == "failure"
    assert memory_payload["error"] == "memory_entry_not_found"
    assert memory_payload["serial_number"] == "999"


def test_action_schema_paths_match_runtime_gpt_api_surface():
    schema_paths = set(_schema()["paths"].keys())
    assert _normalized_api_rule_paths() == schema_paths


def test_action_schema_json_matches_yaml_source():
    assert _schema_json() == _schema()


def test_action_schema_exposes_v31_chat_settings_contract():
    schema = _schema()
    paths = schema["paths"]
    components = schema["components"]["schemas"]

    assert schema["info"]["version"] == "3.5.0"
    assert "/zb_api/chat/options" in paths
    assert "/zb_api/load_memory_logbook_full" not in paths
    assert "/appendMemoryLogbookEntry" not in paths
    assert "/zb_api/koans/{case_id}" in paths
    assert "/zb_api/koans/by-title" in paths
    assert "/zb_api/exemplars/search" in paths
    assert "/zb_api/memory/candidates" in paths
    assert "/zb_api/session-evaluations/review-requests" in paths
    assert "/zb_api/runtime/status-events" in paths
    assert "settings" in components["ChatTurnRequest"]["properties"]
    assert "case_id" in components["ChatTurnRequest"]["properties"]
    assert "solution_notes" in components["KoanResponse"]["properties"]
    assert "409" in paths["/zb_api/chat"]["post"]["responses"]
    assert "session_settings" in components["ChatTurnResponse"]["properties"]
    assert "conversation_status" in components["ChatTurnResponse"]["properties"]
    assert "requested_conversation_id" in components["ChatTurnResponse"]["properties"]
    assert "archived" in components["SaveChatResponse"]["properties"]
    memory_write = paths["/zb_api/update_memory_logbook"]["post"]
    memory_write_schema = memory_write["requestBody"]["content"]["application/json"][
        "schema"
    ]
    assert memory_write["operationId"] == "commitMemoryEntry"
    assert memory_write_schema["required"] == ["entry"]
    assert "full_logbook" not in memory_write_schema["properties"]


def test_koan_lookup_api_by_id_and_title(monkeypatch):
    monkeypatch.setattr(main.utipy.config, "ZB_API_STRICT_AUTH", False)
    client = main.app.test_client()

    by_id = client.get("/zb_api/koans/1")
    by_title = client.get("/zb_api/koans/by-title?title=joshu%27s%20dog")
    with_solution_notes = client.get("/zb_api/koans/46?include_solution_notes=true")
    missing = client.get("/zb_api/koans/999")

    assert by_id.status_code == 200
    assert by_id.get_json()["title"] == "Joshu's Dog"
    assert by_title.status_code == 200
    assert by_title.get_json()["id"] == 1
    assert with_solution_notes.status_code == 200
    assert with_solution_notes.get_json()["solution_notes"] == ["Climb down."]
    assert missing.status_code == 404
    assert missing.get_json()["error"] == "koan_not_found"


def test_scavenged_action_routes_queue_and_report(monkeypatch):
    monkeypatch.setattr(main.utipy.config, "ZB_API_STRICT_AUTH", False)
    monkeypatch.setattr(main.utipy.config, "LOCAL", True)
    monkeypatch.setattr(main.utipy.config, "ACTION_API_TOKEN", "")
    written = []

    def fake_write(blob_name, record, local_path):
        written.append((blob_name, record, local_path))

    monkeypatch.setattr(main.utipy, "_write_jsonl_record", fake_write)
    client = main.app.test_client()
    memory_entry = {
        "date": "2026-04-26",
        "time": "12:00",
        "serial_number": "999",
        "title": "Candidate",
        "koans_used": ["46"],
        "user_problem_or_questions": "test",
        "response_summary": "summary",
        "session_evaluations": [
            {
                "case": "46",
                "conversation_id": "conv-1",
                "evaluation": "Use",
                "notes": "notes",
            }
        ],
        "key_insights": ["insight"],
        "lessons_learned": ["lesson"],
        "final_outcome": "queued",
        "user_instructions": [],
    }

    memory_response = client.post(
        "/zb_api/memory/candidates",
        json={"entry": memory_entry},
    )
    review_response = client.post(
        "/zb_api/session-evaluations/review-requests",
        json={"conversation_id": "conv-1", "reviewer": "ZB", "note": "check"},
    )
    status_response = client.post(
        "/zb_api/runtime/status-events",
        json={"stage": "schema-check", "detail": "ok"},
    )

    assert memory_response.status_code == 200
    memory_payload = memory_response.get_json()
    assert memory_payload["serial_number"] == "999"
    assert memory_payload["canonical_logbook_updated"] is False
    assert memory_payload["queue_blob"] == main.utipy.config.MEMORY_CANDIDATE_QUEUE
    assert review_response.status_code == 200
    assert review_response.get_json()["conversation_id"] == "conv-1"
    assert status_response.status_code == 200
    assert status_response.get_json()["stage"] == "schema-check"
    assert [item[0] for item in written] == [
        main.utipy.config.MEMORY_CANDIDATE_QUEUE,
        main.utipy.config.REVIEW_REQUESTS_BLOB,
    ]


def test_memory_logbook_supports_end_index_pagination(monkeypatch):
    monkeypatch.setattr(main.utipy.config, "ZB_API_STRICT_AUTH", False)
    monkeypatch.setattr(
        main.utipy,
        "load_memory_logbook",
        lambda: [
            {"serial_number": "001", "title": "one"},
            {"serial_number": "002", "title": "two"},
            {"serial_number": "003", "title": "three"},
            {"serial_number": "004", "title": "four"},
        ],
    )
    monkeypatch.setattr(
        utilities,
        "load_memory_logbook",
        main.utipy.load_memory_logbook,
    )
    client = main.app.test_client()

    response = client.get("/zb_api/load_memory_logbook?limit=2&end_index=2")
    payload = response.get_json()

    assert response.status_code == 200
    assert [item["serial_number"] for item in payload["summaries"]] == ["003", "002"]
    assert payload["start_index"] == 1
    assert payload["end_index"] == 2
    assert payload["next_end_index"] == 0
    assert payload["has_more"] is True

    bad = client.get("/zb_api/load_memory_logbook?end_index=9")
    assert bad.status_code == 400


def test_conversation_list_filters_and_paginates(monkeypatch, fake_bucket):
    monkeypatch.setattr(main.utipy.config, "ZB_API_STRICT_AUTH", False)
    monkeypatch.setattr(main.utipy, "BUCKET", fake_bucket)
    monkeypatch.setattr(utilities, "BUCKET", fake_bucket)
    fake_bucket.blob("zbchats/one.jsonl").upload_from_string(
        '{"conversation_id":"one","student":"Ada","case_id":"1","saved_at":"2026-04-19T01:00:00Z","model":"m"}\n'
        '{"role":"user","content":"first"}\n{"role":"assistant","content":"reply"}'
    )
    fake_bucket.blob("zbchats/two.jsonl").upload_from_string(
        '{"conversation_id":"two","student":"Bo","case_id":"2","saved_at":"2026-04-20T01:00:00Z","model":"m"}\n'
        '{"role":"user","content":"second"}\n{"role":"assistant","content":"reply"}'
    )
    client = main.app.test_client()

    response = client.get("/zb_api/conversations/list?case_id=2&limit=1")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["conversation_ids"] == ["two"]
    assert payload["total_matching"] == 1
    assert payload["records"][0]["student"] == "Bo"
    assert payload["records"][0]["date"] == "2026-04-20"
