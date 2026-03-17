import io
import zipfile
from pathlib import Path

import main
from utilities import ArchivedConversation


def test_admin_conversations_download_delete_local_redirects_with_sync_counts(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(main.utipy.config, "LOCAL", True)
    monkeypatch.setattr(main.utipy.config, "ACTION_API_TOKEN", "")

    archives = [
        ArchivedConversation(
            blob_name="zbchats/one.jsonl",
            filename="one.jsonl",
            content=b'{"conversation_id":"one"}\n',
        )
    ]
    deleted: list[str] = []

    monkeypatch.setattr(main.utipy, "download_archived_conversations", lambda: archives)
    monkeypatch.setattr(
        main.utipy,
        "write_archived_conversations_to_directory",
        lambda items, target_dir: [Path(target_dir) / archive.filename for archive in items],
    )
    monkeypatch.setattr(
        main.utipy,
        "delete_archived_conversations",
        lambda blob_names: deleted.extend(list(blob_names)) or len(deleted),
    )
    monkeypatch.setattr(main, "_collected_sessions_web_root", lambda: tmp_path / "web")
    monkeypatch.setattr(
        main,
        "sync_review_queue",
        lambda _repo_root: {
            "records_kept": 7,
            "duplicates_moved": 2,
            "decisions_preserved": 3,
            "evaluations_preserved": 5,
        },
    )

    client = main.app.test_client()
    response = client.post(
        "/admin/conversations/maintenance",
        data={"action": "download_delete"},
        headers={"Accept": "text/html"},
    )

    assert response.status_code == 302
    location = response.headers["Location"]
    assert "status=download_delete" in location
    assert "downloaded=1" in location
    assert "deleted=1" in location
    assert "synced=7" in location
    assert "deduped=2" in location
    assert "preserved=3" in location
    assert "reviewed=5" in location
    assert deleted == ["zbchats/one.jsonl"]


def test_admin_conversations_download_delete_remote_returns_zip(monkeypatch):
    monkeypatch.setattr(main.utipy.config, "LOCAL", False)
    monkeypatch.setattr(main.utipy.config, "ACTION_API_TOKEN", "secret-token")

    archives = [
        ArchivedConversation(
            blob_name="zbchats/alpha.jsonl",
            filename="alpha.jsonl",
            content=b'{"conversation_id":"alpha"}\n',
        )
    ]
    deleted: list[str] = []

    monkeypatch.setattr(main.utipy, "download_archived_conversations", lambda: archives)
    monkeypatch.setattr(
        main.utipy,
        "delete_archived_conversations",
        lambda blob_names: deleted.extend(list(blob_names)) or len(deleted),
    )

    client = main.app.test_client()
    response = client.post(
        "/admin/conversations/maintenance",
        data={"action": "download_delete"},
        headers={"Authorization": "Bearer secret-token"},
    )

    assert response.status_code == 200
    assert response.mimetype == "application/zip"
    assert deleted == ["zbchats/alpha.jsonl"]

    with zipfile.ZipFile(io.BytesIO(response.data), "r") as archive_zip:
        assert archive_zip.namelist() == ["alpha.jsonl"]
        assert archive_zip.read("alpha.jsonl") == b'{"conversation_id":"alpha"}\n'
