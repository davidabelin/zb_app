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
