import json

import main
import session_reviews
import utilities


def test_admin_conversations_backfill_redirects_with_counts(monkeypatch, fake_bucket):
    monkeypatch.setattr(main.utipy.config, "LOCAL", True)
    monkeypatch.setattr(main.utipy.config, "ACTION_API_TOKEN", "")
    monkeypatch.setattr(main.utipy, "BUCKET", fake_bucket)

    utilities.save_chat_to_bucket(
        [
            {"role": "system", "content": "You are Zenbot."},
            {"role": "user", "content": "same transcript"},
            {"role": "assistant", "content": "same reply"},
        ],
        {
            "conversation_id": "one",
            "student": "admin-test",
            "model": "admin-model",
            "saved_at": "2026-03-20T10:00:00Z",
        },
        "zbchats/one.jsonl",
    )

    client = main.app.test_client()
    response = client.post(
        "/admin/conversations/maintenance",
        data={"action": "backfill_review"},
        headers={"Accept": "text/html"},
    )

    assert response.status_code == 302
    location = response.headers["Location"]
    assert "status=backfilled" in location
    assert "scanned=1" in location
    assert "rows=1" in location
    assert "train_rows=0" in location


def test_admin_conversations_shows_review_dashboard(monkeypatch, fake_bucket):
    monkeypatch.setattr(main.utipy.config, "LOCAL", True)
    monkeypatch.setattr(main.utipy.config, "ACTION_API_TOKEN", "")
    monkeypatch.setattr(main.utipy, "BUCKET", fake_bucket)
    monkeypatch.setattr(main.utipy, "load_memory_logbook", lambda: [{"id": "m1"}])

    messages = [
        {"role": "system", "content": "You are Zenbot."},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ]
    params = {
        "conversation_id": "dashboard-one",
        "student": "dash-student",
        "model": "dash-model",
        "saved_at": "2026-03-20T13:00:00Z",
    }
    utilities.save_chat_to_bucket(messages, params, "zbchats/dashboard-one.jsonl")
    session_reviews.upsert_review_record_from_session("dashboard-one", messages, params)

    client = main.app.test_client()
    response = client.get("/admin/conversations")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Session Review Queue" in body
    assert "Open Next CM Review" in body
    assert "routine backfill is not needed" in body
    assert "dashboard-one" in body
    assert "Backend status" in body
    assert "Archive Storage" in body
    assert "Review Manifest" in body
    assert "Memory Logbook" in body
    assert 'href="/admin/logbook"' in body
    assert "Vector Store" in body
    assert "Hot-State Backend" in body
    assert "Need ZB Review" in body
    assert "Not Yet Touched" in body
    assert "Awaiting Other Review" in body
    assert "CM Reviewed" in body
    assert "ZB Reviewed" in body
    assert "Download Memory Logbook" in body
    assert "Sync Local/Remote Sessions" in body
    assert "Backfill Review Manifest" not in body
    assert "Download + delete all." not in body
    assert "Delete all from cloud." not in body
    assert "Session Settings" in body


def test_admin_conversations_rejects_invalid_filter(monkeypatch, fake_bucket):
    monkeypatch.setattr(main.utipy.config, "LOCAL", True)
    monkeypatch.setattr(main.utipy.config, "ACTION_API_TOKEN", "")
    monkeypatch.setattr(main.utipy, "BUCKET", fake_bucket)

    client = main.app.test_client()
    response = client.get("/admin/conversations?evaluation=bogus")

    assert response.status_code == 400


def test_admin_memory_logbook_download_returns_json_attachment(monkeypatch):
    monkeypatch.setattr(main.utipy.config, "LOCAL", True)
    monkeypatch.setattr(main.utipy.config, "ACTION_API_TOKEN", "")
    monkeypatch.setattr(
        main.utipy,
        "load_memory_logbook",
        lambda: [{"serial_number": "001", "summary": "memory"}],
    )

    client = main.app.test_client()
    response = client.get("/admin/memory_logbook/download")

    assert response.status_code == 200
    assert response.mimetype == "application/json"
    assert "attachment" in response.headers["Content-Disposition"]
    assert "memory_logbook.json" in response.headers["Content-Disposition"]
    assert json.loads(response.get_data(as_text=True)) == [
        {"serial_number": "001", "summary": "memory"}
    ]


def test_admin_logbook_requires_auth_before_reading(monkeypatch):
    monkeypatch.setattr(main.utipy.config, "LOCAL", False)
    monkeypatch.setattr(main.utipy.config, "ACTION_API_TOKEN", "secret")

    def unexpected_read():
        raise AssertionError("unauthenticated request read the logbook")

    monkeypatch.setattr(main.utipy, "load_memory_logbook", unexpected_read)
    client = main.app.test_client()

    for path in ("/admin/logbook", "/admin/logbook/1"):
        response = client.get(path)
        assert response.status_code == 302
        assert "/admin/login" in response.headers["Location"]


def test_admin_logbook_index_and_detail_are_readable(monkeypatch):
    monkeypatch.setattr(main.utipy.config, "LOCAL", False)
    monkeypatch.setattr(main.utipy.config, "ACTION_API_TOKEN", "secret")
    entries = [
        utilities.normalize_memory_entry(
            {
                "serial_number": f"{index:03d}",
                "title": f"Memory {index}",
                "date": "2026-10-27",
                "response_summary": f"Summary {index}",
                "key_insights": ["<script>alert('x')</script>"],
                "lessons_learned": ["Keep the full lesson."],
                "user_instructions": ["Remember this instruction."],
            }
        )
        for index in range(1, 23)
    ]
    monkeypatch.setattr(main.utipy, "load_memory_logbook", lambda: entries)
    client = main.app.test_client()
    with client.session_transaction() as admin_session:
        admin_session["admin_authenticated"] = True

    index_response = client.get("/admin/logbook")
    index_body = index_response.get_data(as_text=True)
    assert index_response.status_code == 200
    assert index_response.headers["Cache-Control"] == "private, no-store"
    assert "Summary 22" in index_body
    assert ">Summary 2<" not in index_body
    assert 'href="/admin/logbook/22"' in index_body
    assert 'href="/admin/logbook?page=2"' in index_body

    older_page = client.get("/admin/logbook?page=2")
    assert older_page.status_code == 200
    assert "Summary 2" in older_page.get_data(as_text=True)

    detail_response = client.get("/admin/logbook/22")
    detail_body = detail_response.get_data(as_text=True)
    assert detail_response.status_code == 200
    assert detail_response.headers["Cache-Control"] == "private, no-store"
    assert "Keep the full lesson." in detail_body
    assert "Remember this instruction." in detail_body
    assert "&lt;script&gt;" in detail_body
    assert "<script>alert('x')</script>" not in detail_body
    assert 'href="/admin/logbook/21"' in detail_body
    assert '<button type="button" class="edit-button" disabled' in detail_body
    assert client.post("/admin/logbook/22").status_code == 405


def test_admin_logbook_handles_empty_and_missing_pages(monkeypatch):
    monkeypatch.setattr(main.utipy.config, "LOCAL", True)
    monkeypatch.setattr(main.utipy.config, "ACTION_API_TOKEN", "")
    monkeypatch.setattr(main.utipy, "load_memory_logbook", lambda: [])
    client = main.app.test_client()

    assert "No entries yet" in client.get("/admin/logbook").get_data(as_text=True)
    assert client.get("/admin/logbook?page=bad").status_code == 400
    assert client.get("/admin/logbook?page=2").status_code == 404
    assert client.get("/admin/logbook/1").status_code == 404


def test_admin_memory_logbook_download_is_local_only(monkeypatch):
    monkeypatch.setattr(main.utipy.config, "LOCAL", False)
    monkeypatch.setattr(main.utipy.config, "ACTION_API_TOKEN", "secret")

    client = main.app.test_client()
    response = client.get(
        "/admin/memory_logbook/download",
        headers={"Authorization": "Bearer secret"},
    )

    assert response.status_code == 403


def test_admin_local_remote_sync_runs_broad_local_maintenance(monkeypatch):
    monkeypatch.setattr(main.utipy.config, "LOCAL", True)
    monkeypatch.setattr(main.utipy.config, "ACTION_API_TOKEN", "")

    calls = []

    def fake_download_all():
        calls.append("download_all")
        return True

    def fake_backfill(repo_root):
        calls.append(("backfill", repo_root.name))
        return {
            "transcripts_scanned": 4,
            "review_rows": 3,
            "preserved_existing_reviews": 2,
            "preserved_legacy_reviews": 1,
            "sessions_to_train_rows": 1,
        }

    def fake_sync(repo_root):
        calls.append(("sync", repo_root.name))
        return {
            "records_scanned": 5,
            "records_kept": 4,
            "duplicates_moved": 1,
            "decisions_preserved": 3,
            "generated_rows": 4,
        }

    monkeypatch.setattr(main.utipy, "download_all", fake_download_all)
    monkeypatch.setattr(main.session_reviews, "backfill_review_state", fake_backfill)
    monkeypatch.setattr(main.review_sync, "sync_review_queue", fake_sync)

    client = main.app.test_client()
    response = client.post(
        "/admin/conversations/maintenance",
        data={"action": "sync_local_remote"},
        headers={"Accept": "text/html"},
    )

    assert response.status_code == 302
    location = response.headers["Location"]
    assert "status=synced" in location
    assert "downloaded=1" in location
    assert "rows=3" in location
    assert "records_kept=4" in location
    assert "duplicates_moved=1" in location
    assert calls == ["download_all", ("backfill", "zenbot"), ("sync", "zenbot")]


def test_admin_local_remote_sync_is_local_only(monkeypatch):
    monkeypatch.setattr(main.utipy.config, "LOCAL", False)
    monkeypatch.setattr(main.utipy.config, "ACTION_API_TOKEN", "secret")

    client = main.app.test_client()
    response = client.post(
        "/admin/conversations/maintenance",
        data={"action": "sync_local_remote"},
        headers={"Authorization": "Bearer secret"},
    )

    assert response.status_code == 403


def test_admin_session_settings_placeholder_page(monkeypatch):
    monkeypatch.setattr(main.utipy.config, "LOCAL", True)
    monkeypatch.setattr(main.utipy.config, "ACTION_API_TOKEN", "")

    client = main.app.test_client()
    response = client.get("/admin/session_settings")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Session Settings" in body
    assert "Read-Only Administrative Overview" in body
    assert "This page shows the public dokusan defaults" in body
    assert "/admin/session_settings/presets" in body
