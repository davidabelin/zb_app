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
        "/admin/conversations?evaluation=unreviewed"
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

    response = client.post("/save_chat", json={"conversation_id": "save-me"})
    assert response.status_code == 200

    rows = session_reviews.load_review_state()
    assert len(rows) == 1
    assert rows[0]["conversation_id"] == "save-me"
