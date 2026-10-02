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
            "model_name": utilities.config.MODEL_NAME,
            "reasoning_effort": "",
            "temperature": 0.9,
            "top_p": 1.0,
            "max_output_tokens": 1000,
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


def _seed_review_records(fake_bucket, count=4):
    for index in range(count):
        _archive_review_record(
            fake_bucket,
            conversation_id=f"review-api-{index + 1:03}",
        )

    rows = session_reviews.load_review_state()
    if len(rows) > 0:
        rows[0] = session_reviews.apply_reviewer_decision(rows[0], "ZB", "Use")
    if len(rows) > 1:
        rows[1] = session_reviews.apply_reviewer_decision(rows[1], "CM", "Alter")
    if len(rows) > 2:
        rows[2] = session_reviews.apply_reviewer_decision(rows[2], "ZB", "Reject")
        rows[2] = session_reviews.apply_reviewer_decision(rows[2], "CM", "Reject")
    session_reviews.save_review_state(rows)
    return session_reviews.load_review_state()


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
    assert zb_payload["summary"]["unreviewed"] == 1
    assert zb_payload["progress_summary"]["zb_reviewed"] == 1
    assert zb_payload["progress_summary"]["awaiting_other_review"] == 1

    cm_response = client.post(
        "/zb_api/session-evaluations/record/0/decision",
        headers=headers,
        json={
            "conversation_id": "bridge-test-001",
            "reviewer": "CM",
            "evaluation": "Use",
        },
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
    assert summary_payload["progress_summary"]["zb_reviewed"] == 1
    assert summary_payload["progress_summary"]["cm_reviewed"] == 1
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


def test_api_review_decision_rejects_stale_conversation_id(monkeypatch, fake_bucket):
    monkeypatch.setattr(main.utipy.config, "LOCAL", True)
    monkeypatch.setattr(main.utipy.config, "ACTION_API_TOKEN", "secret-token")
    monkeypatch.setattr(main.utipy, "BUCKET", fake_bucket)

    _archive_review_record(fake_bucket, conversation_id="guard-test-001")
    headers = {"Authorization": "Bearer secret-token"}
    client = main.app.test_client()

    response = client.post(
        "/zb_api/session-evaluations/record/0/decision",
        headers=headers,
        json={
            "conversation_id": "different-record",
            "reviewer": "ZB",
            "evaluation": "Reject",
        },
    )
    payload = response.get_json()

    assert response.status_code == 409
    assert payload["error"] == "review_record_mismatch"
    rows = session_reviews.load_review_state()
    assert rows[0]["review_zb"] == ""


def test_needs_zb_review_lists_summary_records_with_pagination(
    monkeypatch, fake_bucket
):
    monkeypatch.setattr(main.utipy.config, "LOCAL", True)
    monkeypatch.setattr(main.utipy.config, "ACTION_API_TOKEN", "secret-token")
    monkeypatch.setattr(main.utipy, "BUCKET", fake_bucket)

    _seed_review_records(fake_bucket)
    headers = {"Authorization": "Bearer secret-token"}
    client = main.app.test_client()

    response = client.get(
        "/zb_api/session-evaluations/needs-zb-review?limit=1",
        headers=headers,
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["reviewer"] == "ZB"
    assert payload["total_matching"] == 2
    assert payload["offset"] == 0
    assert payload["limit"] == 1
    assert payload["has_more"] is True
    assert len(payload["records"]) == 1
    assert payload["records"][0]["review_zb"] is None
    assert "messages" not in payload["records"][0]

    second_page = client.get(
        "/zb_api/session-evaluations/needs-zb-review?offset=1&limit=1",
        headers=headers,
    ).get_json()
    assert second_page["has_more"] is False
    assert len(second_page["records"]) == 1
    assert second_page["records"][0]["index"] != payload["records"][0]["index"]


def test_random_zb_review_returns_full_record_and_respects_exclusions(
    monkeypatch, fake_bucket
):
    monkeypatch.setattr(main.utipy.config, "LOCAL", True)
    monkeypatch.setattr(main.utipy.config, "ACTION_API_TOKEN", "secret-token")
    monkeypatch.setattr(main.utipy, "BUCKET", fake_bucket)

    rows = _seed_review_records(fake_bucket)
    candidates = session_reviews.indices_needing_reviewer(rows, "ZB")
    selected_index = candidates[0]
    excluded = ",".join(str(index) for index in candidates[1:])

    headers = {"Authorization": "Bearer secret-token"}
    client = main.app.test_client()
    response = client.get(
        f"/zb_api/session-evaluations/random-zb-review?exclude_indices={excluded}",
        headers=headers,
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["reviewer"] == "ZB"
    assert payload["available_count"] == 1
    assert payload["selected_index"] == selected_index
    assert payload["record"]["index"] == selected_index
    assert payload["record"]["review_zb"] is None
    assert payload["record"]["messages"]


def test_random_zb_review_returns_null_when_no_candidates(
    monkeypatch, fake_bucket
):
    monkeypatch.setattr(main.utipy.config, "LOCAL", True)
    monkeypatch.setattr(main.utipy.config, "ACTION_API_TOKEN", "secret-token")
    monkeypatch.setattr(main.utipy, "BUCKET", fake_bucket)

    rows = _seed_review_records(fake_bucket, count=2)
    for index, row in enumerate(rows):
        rows[index] = session_reviews.apply_reviewer_decision(row, "ZB", "Use")
    session_reviews.save_review_state(rows)

    headers = {"Authorization": "Bearer secret-token"}
    client = main.app.test_client()
    response = client.get(
        "/zb_api/session-evaluations/random-zb-review",
        headers=headers,
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["available_count"] == 0
    assert payload["selected_index"] is None
    assert payload["record"] is None


def test_random_sample_returns_unique_summary_records_and_respects_exclusions(
    monkeypatch, fake_bucket
):
    monkeypatch.setattr(main.utipy.config, "LOCAL", True)
    monkeypatch.setattr(main.utipy.config, "ACTION_API_TOKEN", "secret-token")
    monkeypatch.setattr(main.utipy, "BUCKET", fake_bucket)

    _seed_review_records(fake_bucket, count=5)
    headers = {"Authorization": "Bearer secret-token"}
    client = main.app.test_client()
    response = client.get(
        "/zb_api/session-evaluations/random-sample?count=3&exclude_indices=0,1",
        headers=headers,
    )
    payload = response.get_json()
    returned_indices = [record["index"] for record in payload["records"]]

    assert response.status_code == 200
    assert payload["requested_count"] == 3
    assert payload["sampled_count"] == 3
    assert payload["available_count"] == 3
    assert len(returned_indices) == len(set(returned_indices))
    assert not ({0, 1} & set(returned_indices))
    assert all("messages" not in record for record in payload["records"])


def test_review_random_query_validation_returns_bad_request(
    monkeypatch, fake_bucket
):
    monkeypatch.setattr(main.utipy.config, "LOCAL", True)
    monkeypatch.setattr(main.utipy.config, "ACTION_API_TOKEN", "secret-token")
    monkeypatch.setattr(main.utipy, "BUCKET", fake_bucket)

    _seed_review_records(fake_bucket, count=1)
    headers = {"Authorization": "Bearer secret-token"}
    client = main.app.test_client()

    bad_count = client.get(
        "/zb_api/session-evaluations/random-sample?count=26",
        headers=headers,
    )
    bad_exclude = client.get(
        "/zb_api/session-evaluations/random-zb-review?exclude_indices=1,nope",
        headers=headers,
    )
    bad_offset = client.get(
        "/zb_api/session-evaluations/needs-zb-review?offset=-1",
        headers=headers,
    )

    for response in (bad_count, bad_exclude, bad_offset):
        payload = response.get_json()
        assert response.status_code == 400
        assert payload["status"] == "failure"
        assert payload["error"] == "bad_request"


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
    assert "Reopen saved record." in body
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
            "model_name": utilities.config.MODEL_NAME,
            "reasoning_effort": "",
            "temperature": 0.9,
            "top_p": 1.0,
            "max_output_tokens": 1000,
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
