import json

import session_reviews
import utilities


def _review_row(conversation_id, review_zb="", review_cm="", evaluation=""):
    return {
        "conversation_id": conversation_id,
        "transcript_blob_name": f"zbchats/{conversation_id}.jsonl",
        "transcript_hash": f"hash-{conversation_id}",
        "message_count": 3,
        "preview_user": f"user {conversation_id}",
        "preview_assistant": f"assistant {conversation_id}",
        "review_version": 2,
        "review_zb": review_zb,
        "review_cm": review_cm,
        "evaluation": evaluation,
    }


def test_apply_reviewer_decision_recomputes_final_from_dual_reviews():
    row = {
        "conversation_id": "row-1",
        "transcript_blob_name": "zbchats/row-1.jsonl",
        "transcript_hash": "abc123",
        "message_count": 3,
        "preview_user": "hello",
        "preview_assistant": "world",
        "review_version": 2,
        "review_zb": "Use",
        "review_cm": "Reject",
        "evaluation": "Alter",
    }

    updated = session_reviews.apply_reviewer_decision(row, "CM", "Use")

    assert updated["review_cm"] == "Use"
    assert updated["evaluation"] == "Use"


def test_single_alter_waits_for_other_review_and_counts_as_pending():
    row = _review_row("single-alter", review_cm="Alter", evaluation="Alter")
    normalized = session_reviews._normalize_review_row(row)

    assert normalized["evaluation"] == ""
    assert session_reviews.status_label(normalized) == "Awaiting Other Review"
    assert session_reviews.build_summary([row])["Alter"] == 0
    assert session_reviews.build_progress_summary([row])["awaiting_other_review"] == 1


def test_concurrent_review_write_retries_without_losing_other_decision(
    monkeypatch, fake_bucket
):
    monkeypatch.setattr(utilities, "BUCKET", fake_bucket)
    for conversation_id in ("concurrent-a", "concurrent-b"):
        messages = [{"role": "user", "content": "hello"}]
        fake_bucket.blob(f"zbchats/{conversation_id}.jsonl").upload_from_string(
            json.dumps(messages[0]) + "\n"
        )
        session_reviews.upsert_review_record_from_session(
            conversation_id, messages, {"conversation_id": conversation_id}
        )

    original_write = session_reviews._write_jsonl_blob
    injected = False

    def concurrent_write(name, rows, **kwargs):
        nonlocal injected
        if name == session_reviews.REVIEW_INDEX_BLOB and not injected:
            injected = True
            current = session_reviews.load_review_state()
            current[0] = session_reviews.apply_reviewer_decision(
                current[0], "ZB", "Reject"
            )
            original_write(name, current)
        return original_write(name, rows, **kwargs)

    monkeypatch.setattr(session_reviews, "_write_jsonl_blob", concurrent_write)
    rows, index, synced = session_reviews.save_reviewer_decision(
        "concurrent-a", "CM", "Use"
    )

    assert synced
    assert rows[index]["review_cm"] == "Use"
    assert sum(bool(row["review_zb"]) for row in rows) == 1
    assert sum(bool(row["review_cm"]) for row in rows) == 1


def test_export_failure_does_not_report_a_saved_decision_as_failed(
    monkeypatch, fake_bucket
):
    monkeypatch.setattr(utilities, "BUCKET", fake_bucket)
    messages = [{"role": "user", "content": "hello"}]
    fake_bucket.blob("zbchats/export-failure.jsonl").upload_from_string(
        json.dumps(messages[0]) + "\n"
    )
    session_reviews.upsert_review_record_from_session(
        "export-failure", messages, {"conversation_id": "export-failure"}
    )
    original_write = session_reviews._write_jsonl_blob

    def fail_export(name, rows, **kwargs):
        if name == session_reviews.SESSIONS_TO_TRAIN_BLOB:
            raise RuntimeError("simulated export outage")
        return original_write(name, rows, **kwargs)

    monkeypatch.setattr(session_reviews, "_write_jsonl_blob", fail_export)
    rows, index, synced = session_reviews.save_reviewer_decision(
        "export-failure", "CM", "Reject"
    )

    assert not synced
    assert rows[index]["review_cm"] == "Reject"
    assert session_reviews.load_review_state()[index]["review_cm"] == "Reject"


def test_build_progress_summary_counts_partial_reviews():
    rows = [
        {
            "conversation_id": "row-1",
            "transcript_blob_name": "zbchats/row-1.jsonl",
            "transcript_hash": "abc123",
            "message_count": 3,
            "preview_user": "hello",
            "preview_assistant": "world",
            "review_version": 2,
            "review_zb": "",
            "review_cm": "",
            "evaluation": "",
        },
        {
            "conversation_id": "row-2",
            "transcript_blob_name": "zbchats/row-2.jsonl",
            "transcript_hash": "def456",
            "message_count": 3,
            "preview_user": "hello",
            "preview_assistant": "world",
            "review_version": 2,
            "review_zb": "Use",
            "review_cm": "",
            "evaluation": "",
        },
        {
            "conversation_id": "row-3",
            "transcript_blob_name": "zbchats/row-3.jsonl",
            "transcript_hash": "ghi789",
            "message_count": 3,
            "preview_user": "hello",
            "preview_assistant": "world",
            "review_version": 2,
            "review_zb": "Reject",
            "review_cm": "Reject",
            "evaluation": "Reject",
        },
    ]

    progress = session_reviews.build_progress_summary(rows)

    assert progress == {
        "cm_reviewed": 1,
        "zb_reviewed": 2,
        "needs_cm_review": 2,
        "needs_zb_review": 1,
        "awaiting_other_review": 1,
        "not_started": 1,
    }


def test_dashboard_review_filters_match_dual_review_states():
    rows = [
        _review_row("not-started"),
        _review_row("zb-only", review_zb="Use"),
        _review_row("cm-only", review_cm="Use"),
        _review_row("final-use", review_zb="Use", review_cm="Use", evaluation="Use"),
        _review_row(
            "final-reject",
            review_zb="Reject",
            review_cm="Reject",
            evaluation="Reject",
        ),
    ]

    def ids_for(filter_name):
        return [
            row["conversation_id"]
            for row in session_reviews.list_dashboard_rows(rows, filter_name)
        ]

    assert ids_for("not_started") == ["not-started"]
    assert ids_for("awaiting_other_review") == ["zb-only", "cm-only"]
    assert ids_for("cm_reviewed") == ["cm-only", "final-use", "final-reject"]
    assert ids_for("zb_reviewed") == ["zb-only", "final-use", "final-reject"]
    assert ids_for("needs_zb_review") == ["not-started", "cm-only"]
    assert ids_for("unreviewed") == ["not-started", "zb-only", "cm-only"]
    assert ids_for("Reject") == ["final-reject"]


def test_backfill_review_state_preserves_legacy_review_by_transcript_hash(
    monkeypatch, fake_bucket, tmp_path
):
    monkeypatch.setattr(utilities.config, "LOCAL", True)
    monkeypatch.setattr(utilities, "BUCKET", fake_bucket)

    messages = [
        {"role": "system", "content": "You are Zenbot."},
        {"role": "user", "content": "legacy user"},
        {"role": "assistant", "content": "legacy reply"},
    ]
    params = {
        "conversation_id": "legacy-preserve",
        "saved_at": "2026-03-20T09:00:00Z",
    }
    utilities.save_chat_to_bucket(messages, params, "zbchats/legacy-preserve.jsonl")

    reviewed_path = (
        tmp_path
        / "training"
        / "generated"
        / "collected_sessions_with_evaluations.jsonl"
    )
    reviewed_path.parent.mkdir(parents=True, exist_ok=True)
    reviewed_path.write_text(
        json.dumps(
            {
                "messages": messages,
                "metadata": {"conversation_id": "legacy-preserve"},
                "review_version": 2,
                "review_zb": "Reject",
                "review_cm": "Reject",
                "evaluation": "Reject",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    summary = session_reviews.backfill_review_state(tmp_path)
    rows = session_reviews.load_review_state()

    assert summary["preserved_legacy_reviews"] == 1
    assert len(rows) == 1
    assert rows[0]["conversation_id"] == "legacy-preserve"
    assert rows[0]["review_zb"] == "Reject"
    assert rows[0]["review_cm"] == "Reject"
    assert rows[0]["evaluation"] == "Reject"
