from __future__ import annotations

import re
from pathlib import Path

import yaml

import main
import utilities


def _schema() -> dict:
    schema_path = (
        Path(__file__).resolve().parents[2]
        / "zenbot_knowledge"
        / "action_schemas.yaml"
    )
    return yaml.safe_load(schema_path.read_text(encoding="utf-8"))


def _normalized_api_rule_paths() -> set[str]:
    paths: set[str] = set()
    for rule in main.app.url_map.iter_rules():
        path = rule.rule
        if not (path.startswith("/zb_api/") or path == "/appendMemoryLogbookEntry"):
            continue
        normalized = re.sub(r"<(?:[^:>]+:)?([^>]+)>", r"{\1}", path)
        paths.add(normalized)
    return paths


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
        settings={"preset_id": "balanced_mumon"},
    )

    client = main.app.test_client()
    response = client.post(
        "/zb_api/chat",
        json={
            "conversation_id": conversation_id,
            "message": "hello",
            "settings": {"preset_id": "explanatory_guide"},
        },
    )

    payload = response.get_json()
    assert response.status_code == 409
    assert payload["status"] == "failure"
    assert payload["error"] == "settings_locked"
    assert payload["conversation_id"] == conversation_id
    assert payload["session_settings"]["preset_id"] == "balanced_mumon"


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
                "model_name": "gpt-5.4-mini",
                "profile": "live",
                "training_loss": 0.0,
                "botling_id": "balanced_mumon",
                "settings_version": "v3.1",
                "session_settings": utilities.resolve_session_settings(None),
            },
        ),
    )
    monkeypatch.setattr(main.utipy, "delete_messages_from_firestore", lambda conversation_id: None)
    monkeypatch.setattr(main.utipy, "submit_background_session_critic", lambda *args, **kwargs: "resp_123")

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
    save_response = client.post("/zb_api/save_chat", json={"conversation_id": "missing"})

    for response in (archive_response, review_response, save_response):
        payload = response.get_json()
        assert response.status_code == 503
        assert payload["status"] == "failure"
        assert payload["error"] == "storage_unavailable"


def test_standardized_not_found_for_conversation_and_memory_entry(monkeypatch, fake_bucket):
    monkeypatch.setattr(main.utipy.config, "ZB_API_STRICT_AUTH", False)
    monkeypatch.setattr(main.utipy, "BUCKET", fake_bucket)
    monkeypatch.setattr(utilities, "BUCKET", fake_bucket)
    monkeypatch.setattr(main.utipy, "get_memory_logbook_entry", lambda serial_number: None)

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


def test_action_schema_exposes_v31_chat_settings_contract():
    schema = _schema()
    paths = schema["paths"]
    components = schema["components"]["schemas"]

    assert "/zb_api/chat/options" in paths
    assert "/zb_api/load_memory_logbook_full" in paths
    assert "settings" in components["ChatTurnRequest"]["properties"]
    assert "409" in paths["/zb_api/chat"]["post"]["responses"]
    assert "session_settings" in components["ChatTurnResponse"]["properties"]
    assert "conversation_status" in components["ChatTurnResponse"]["properties"]
    assert "requested_conversation_id" in components["ChatTurnResponse"]["properties"]
    assert "archived" in components["SaveChatResponse"]["properties"]
