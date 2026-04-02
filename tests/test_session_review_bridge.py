import json

import main
import session_reviews
import utilities


def _archive_review_record(fake_bucket, conversation_id="bridge-test-001"):
    messages = [
        {"role": "system", "content": "You are Zenbot."},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ]
    params = {
        "conversation_id": conversation_id,
        "student": "bridge-student",
        "case_id": "11",
        "model": "bridge-model",
        "profile": "mid",
        "botling_id": "fierce_barrier",
        "settings_version": "v3.1",
        "session_settings": {
            "preset_id": "fierce_barrier",
            "model_name": "gpt-5.4-mini",
            "reasoning_effort": "medium",
            "temperature": None,
            "top_p": None,
            "max_output_tokens": 700,
            "enable_function_tools": True,
            "enable_file_search": False,
            "enable_web_search": False,
            "enable_background_critic": False,
        },
        "loss": 0.25,
        "saved_at": "2026-03-20T12:00:00Z",
    }
    utilities.save_chat_to_bucket(
        messages,
        params,
        f"{session_reviews.TRANSCRIPTS_PREFIX}{conversation_id}.jsonl",
    )
    session_reviews.upsert_review_record_from_session(conversation_id, messages, params)
    return messages


def test_admin_review_redirects_to_cloud_review_dashboard(monkeypatch):
    monkeypatch.setattr(main.utipy.config, "LOCAL", True)
    monkeypatch.setattr(main.utipy.config, "ACTION_API_TOKEN", "")

    client = main.app.test_client()
    response = client.get("/admin/review")

    assert response.status_code == 302
    assert response.headers["Location"].endswith(
        "/admin/conversations?evaluation=needs_cm_review"
    )


def test_review_page_and_api_use_cloud_review_manifest(monkeypatch, fake_bucket):
    monkeypatch.setattr(main.utipy.config, "LOCAL", True)
    monkeypatch.setattr(main.utipy.config, "ACTION_API_TOKEN", "secret-token")
    monkeypatch.setattr(main.utipy, "BUCKET", fake_bucket)

    messages = _archive_review_record(fake_bucket)
    headers = {"Authorization": "Bearer secret-token"}
    client = main.app.test_client()

    review_response = client.get("/review", headers=headers)
    review_body = review_response.get_data(as_text=True)
    assert review_response.status_code == 200
    assert "Session Evaluation Review" in review_body
    assert "bridge-test-001" in review_body
    assert session_reviews.REVIEW_INDEX_BLOB in review_body

    old_path_response = client.get("/api/session-evaluations/summary", headers=headers)
    assert old_path_response.status_code == 404

    zb_response = client.post(
        "/zb_api/session-evaluations/record/0/decision",
        headers=headers,
        json={"reviewer": "ZB", "evaluation": "Use"},
    )
    assert zb_response.status_code == 200
    zb_payload = zb_response.get_json()
    assert zb_payload["record"]["review_zb"] == "Use"
    assert zb_payload["record"]["evaluation"] == ""
    assert zb_payload["record"]["status_label"] == "Awaiting Other Review"

    cm_response = client.post(
        "/zb_api/session-evaluations/record/0/decision",
        headers=headers,
        json={"reviewer": "CM", "evaluation": "Use"},
    )
    assert cm_response.status_code == 200
    cm_payload = cm_response.get_json()
    assert cm_payload["record"]["review_cm"] == "Use"
    assert cm_payload["record"]["evaluation"] == "Use"

    summary_response = client.get("/zb_api/session-evaluations/summary", headers=headers)
    summary_payload = summary_response.get_json()
    assert summary_response.status_code == 200
    assert summary_payload["summary"]["Use"] == 1
    assert summary_payload["summary"]["unreviewed"] == 0
    assert summary_payload["storage"]["bucket"] == main.utipy.config.BUCKET_NAME
    assert summary_payload["storage"]["review_index_blob"] == session_reviews.REVIEW_INDEX_BLOB

    train_rows = [
        json.loads(line)
        for line in fake_bucket.storage[session_reviews.SESSIONS_TO_TRAIN_BLOB]
        .decode("utf-8")
        .splitlines()
        if line
    ]
    assert train_rows == [{"messages": messages}]

    rows = session_reviews.load_review_state()
    record = session_reviews.serialize_record(rows, 0)
    assert record["metadata"]["botling_id"] == "fierce_barrier"
    assert record["metadata"]["settings_version"] == "v3.1"
    assert record["metadata"]["session_settings"]["preset_id"] == "fierce_barrier"


def test_browser_decision_leaves_cm_queue_after_save(monkeypatch, fake_bucket):
    monkeypatch.setattr(main.utipy.config, "LOCAL", True)
    monkeypatch.setattr(main.utipy.config, "ACTION_API_TOKEN", "secret-token")
    monkeypatch.setattr(main.utipy, "BUCKET", fake_bucket)

    _archive_review_record(fake_bucket, conversation_id="browser-cm-001")
    headers = {"Authorization": "Bearer secret-token"}
    client = main.app.test_client()

    response = client.post(
        "/review/record/0/decision",
        headers=headers,
        data={
            "dashboard_filter": "needs_cm_review",
            "reviewer": "CM",
            "evaluation": "Use",
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"].startswith(
        "/admin/conversations?evaluation=needs_cm_review"
    )

    queue_response = client.get(
        "/admin/conversations?evaluation=needs_cm_review", headers=headers
    )
    queue_body = queue_response.get_data(as_text=True)
    assert queue_response.status_code == 200
    assert "browser-cm-001" not in queue_body

    rows = session_reviews.load_review_state()
    assert rows[0]["review_cm"] == "Use"


def test_browser_review_page_confirms_saved_pending_reject(monkeypatch, fake_bucket):
    monkeypatch.setattr(main.utipy.config, "LOCAL", True)
    monkeypatch.setattr(main.utipy.config, "ACTION_API_TOKEN", "secret-token")
    monkeypatch.setattr(main.utipy, "BUCKET", fake_bucket)

    _archive_review_record(fake_bucket, conversation_id="browser-reject-001")
    headers = {"Authorization": "Bearer secret-token"}
    client = main.app.test_client()

    response = client.post(
        "/review/record/0/decision",
        headers=headers,
        data={
            "dashboard_filter": "needs_cm_review",
            "reviewer": "CM",
            "evaluation": "Reject",
        },
        follow_redirects=True,
    )

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Saved CM Reject" in body
    assert "browser-reject-001" in body
    assert "Final Use/Alter/Reject counts only change after both reviewer decisions are present." in body
    assert "Awaiting Other Review" in body

    revisit = client.get("/review?index=0&evaluation=needs_cm_review", headers=headers)
    revisit_body = revisit.get_data(as_text=True)
    assert revisit.status_code == 200
    assert 'CM review <strong>Reject</strong>' in revisit_body


def test_save_chat_upserts_manifest_without_duplicate_rows(monkeypatch, fake_bucket):
    monkeypatch.setattr(main.utipy.config, "LOCAL", True)
    monkeypatch.setattr(main.utipy, "BUCKET", fake_bucket)

    messages = [
        {"role": "system", "content": "You are Zenbot."},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "reply"},
    ]
    metadata = {
        "student": "save-test",
        "case_id": "8",
        "model_name": "save-model",
        "profile": "high",
        "training_loss": 0.5,
        "botling_id": "balanced_mumon",
        "settings_version": "v3.1",
        "session_settings": {
            "preset_id": "balanced_mumon",
            "model_name": "gpt-5.4-mini",
            "reasoning_effort": "low",
            "temperature": None,
            "top_p": None,
            "max_output_tokens": 900,
            "enable_function_tools": True,
            "enable_file_search": False,
            "enable_web_search": False,
            "enable_background_critic": False,
        },
    }

    monkeypatch.setattr(
        main.utipy,
        "get_conversation_state",
        lambda conversation_id: (messages, dict(metadata)),
    )
    monkeypatch.setattr(main.utipy, "delete_messages_from_firestore", lambda conversation_id: None)

    client = main.app.test_client()
    response = client.post("/save_chat", json={"conversation_id": "save-me"})
    assert response.status_code == 200

    rows = session_reviews.load_review_state()
    assert len(rows) == 1
    assert rows[0]["conversation_id"] == "save-me"
    assert rows[0]["botling_id"] == "balanced_mumon"
    assert rows[0]["session_settings"]["preset_id"] == "balanced_mumon"

    response = client.post("/save_chat", json={"conversation_id": "save-me"})
    assert response.status_code == 200

    rows = session_reviews.load_review_state()
    assert len(rows) == 1
    assert rows[0]["conversation_id"] == "save-me"
