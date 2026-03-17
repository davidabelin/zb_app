import csv
import json
from pathlib import Path

from review_sync import sync_review_queue


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_sync_review_queue_quarantines_duplicates_and_preserves_keep(tmp_path):
    repo_root = tmp_path
    review_csv = repo_root / "training" / "trainset04" / "review.csv"
    reviewed_jsonl = (
        repo_root / "training" / "generated" / "collected_sessions_with_evaluations.jsonl"
    )

    primary = repo_root / "collected_sessions" / "local" / "zbchat-keep.jsonl"
    duplicate = repo_root / "collected_sessions" / "web" / "zbchat-keep-short.jsonl"
    unique = repo_root / "collected_sessions" / "web" / "zbchat-unique.jsonl"

    duplicate_rows = [
        {"conversation_id": "zbchat-keep", "model": "gpt-4", "student": "web"},
        {"role": "system", "content": "You are Zenbot."},
        {"role": "user", "content": "Same transcript"},
        {"role": "assistant", "content": "Same reply"},
    ]
    _write_jsonl(primary, duplicate_rows)
    _write_jsonl(duplicate, duplicate_rows)
    _write_jsonl(
        unique,
        [
            {"conversation_id": "zbchat-unique", "model": "gpt-4", "student": "web"},
            {"role": "system", "content": "You are Zenbot."},
            {"role": "user", "content": "Unique transcript"},
            {"role": "assistant", "content": "Unique reply"},
        ],
    )

    review_csv.parent.mkdir(parents=True, exist_ok=True)
    with review_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "id",
                "keep",
                "conversation_id",
                "source_path",
                "model",
                "student",
                "num_messages",
                "preview_user",
                "preview_assistant",
                "bad_json_lines",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "id": "old-id",
                "keep": "1",
                "conversation_id": "zbchat-keep",
                "source_path": "collected_sessions/web/zbchat-keep-short.jsonl",
                "model": "gpt-4",
                "student": "web",
                "num_messages": "3",
                "preview_user": "Same transcript",
                "preview_assistant": "Same reply",
                "bad_json_lines": "0",
            }
        )

    _write_jsonl(
        reviewed_jsonl,
        [
            {
                "messages": [
                    {"role": "system", "content": "You are Zenbot."},
                    {"role": "user", "content": "Same transcript"},
                    {"role": "assistant", "content": "Same reply"},
                ],
                "metadata": {
                    "conversation_id": "zbchat-keep",
                    "source_path": "collected_sessions/web/zbchat-keep-short.jsonl",
                    "source_subdir": "web",
                },
                "evaluation": "Use",
            }
        ],
    )

    summary = sync_review_queue(repo_root)

    assert summary["records_scanned"] == 3
    assert summary["records_kept"] == 2
    assert summary["duplicates_moved"] == 1
    assert summary["decisions_preserved"] == 1
    assert summary["generated_rows"] == 2
    assert summary["evaluations_preserved"] == 1
    assert summary["train_rows"] == 1

    moved_duplicate = (
        repo_root
        / "collected_sessions"
        / "_duplicates"
        / "web"
        / "zbchat-keep-short.jsonl"
    )
    assert moved_duplicate.exists()
    assert not duplicate.exists()

    with review_csv.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 2
    keep_row = next(row for row in rows if row["conversation_id"] == "zbchat-keep")
    unique_row = next(row for row in rows if row["conversation_id"] == "zbchat-unique")
    assert keep_row["source_path"] == "collected_sessions/local/zbchat-keep.jsonl"
    assert keep_row["keep"] == "1"
    assert unique_row["keep"] == ""

    reviewed_rows = [
        json.loads(line)
        for line in reviewed_jsonl.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    keep_reviewed = next(
        row
        for row in reviewed_rows
        if row["metadata"]["conversation_id"] == "zbchat-keep"
    )
    assert keep_reviewed["metadata"]["source_path"] == (
        "collected_sessions/local/zbchat-keep.jsonl"
    )
    assert keep_reviewed["review_version"] == 1
    assert keep_reviewed["review_zb"] == ""
    assert keep_reviewed["review_cm"] == "Use"
    assert keep_reviewed["evaluation"] == "Use"
