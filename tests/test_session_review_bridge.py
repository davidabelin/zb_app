import json

import main


def _write_jsonl(path, rows):
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_admin_review_redirects_to_dual_review_and_syncs(monkeypatch):
    monkeypatch.setattr(main.utipy.config, "LOCAL", True)
    monkeypatch.setattr(main.utipy.config, "ACTION_API_TOKEN", "")

    sync_calls = []
    monkeypatch.setattr(
        main,
        "sync_review_queue",
        lambda repo_root: sync_calls.append(repo_root) or {"records_kept": 0},
    )

    client = main.app.test_client()
    response = client.get("/admin/review")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/review")
    assert sync_calls == [main._repo_root()]


def test_review_page_and_api_reuse_dual_review_workflow(monkeypatch, tmp_path):
    monkeypatch.setattr(main.utipy.config, "LOCAL", True)
    monkeypatch.setattr(main.utipy.config, "ACTION_API_TOKEN", "")

    source_path = tmp_path / "collected_sessions_with_metadata.jsonl"
    reviewed_path = tmp_path / "collected_sessions_with_evaluations.jsonl"
    train_path = tmp_path / "sessions_to_train.jsonl"

    _write_jsonl(
        source_path,
        [
            {
                "messages": [
                    {"role": "system", "content": "You are Zenbot."},
                    {"role": "user", "content": "hello"},
                    {"role": "assistant", "content": "world"},
                ],
                "metadata": {
                    "conversation_id": "bridge-test-001",
                    "source_path": "collected_sessions/web/bridge-test-001.jsonl",
                    "source_subdir": "web",
                },
            }
        ],
    )

    monkeypatch.setitem(main.app.config, "SOURCE_JSONL", str(source_path))
    monkeypatch.setitem(main.app.config, "REVIEWED_JSONL", str(reviewed_path))
    monkeypatch.setitem(main.app.config, "TRAIN_JSONL", str(train_path))

    client = main.app.test_client()

    review_response = client.get("/review")
    review_body = review_response.get_data(as_text=True)
    assert review_response.status_code == 200
    assert "Session Evaluation Review" in review_body
    assert "bridge-test-001" in review_body

    zb_response = client.post(
        "/api/session-evaluations/record/0/decision",
        json={"reviewer": "ZB", "evaluation": "Use"},
    )
    assert zb_response.status_code == 200
    zb_payload = zb_response.get_json()
    assert zb_payload["record"]["review_zb"] == "Use"
    assert zb_payload["record"]["evaluation"] == ""

    cm_response = client.post(
        "/api/session-evaluations/record/0/decision",
        json={"reviewer": "CM", "evaluation": "Use"},
    )
    assert cm_response.status_code == 200
    cm_payload = cm_response.get_json()
    assert cm_payload["record"]["review_cm"] == "Use"
    assert cm_payload["record"]["evaluation"] == "Use"

    summary_response = client.get("/api/session-evaluations/summary")
    summary_payload = summary_response.get_json()
    assert summary_response.status_code == 200
    assert summary_payload["summary"]["Use"] == 1
    assert summary_payload["summary"]["unreviewed"] == 0

    train_rows = [json.loads(line) for line in train_path.read_text(encoding="utf-8").splitlines() if line]
    assert train_rows == [
        {
            "messages": [
                {"role": "system", "content": "You are Zenbot."},
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "world"},
            ]
        }
    ]
