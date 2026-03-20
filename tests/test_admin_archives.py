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
    assert "Backfill Review Manifest" not in body
    assert "Download + delete all." not in body
    assert "Delete all from cloud." not in body
