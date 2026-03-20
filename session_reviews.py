"""Cloud-backed session review storage and record helpers.

This module replaces the older local-file review bridge with a review manifest
stored in Google Cloud Storage alongside the archived conversation transcripts.
It keeps one manifest row per archived conversation, regenerates the training
export for accepted sessions, and exposes the normalization and navigation
helpers used by both the browser admin UI and the GPT-facing review API.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import utilities as utipy


REVIEW_CHOICES = ("Use", "Alter", "Reject")
REVIEWERS = ("ZB", "CM")
FORM_REVIEWER_DEFAULT = "CM"
DUAL_REVIEW_VERSION = 2
TRANSCRIPTS_PREFIX = "zbchats/"
REVIEW_INDEX_BLOB = "session_reviews/index.jsonl"
SESSIONS_TO_TRAIN_BLOB = "session_reviews/sessions_to_train.jsonl"
_ROLE_SET = {"system", "user", "assistant"}


def storage_metadata() -> dict[str, str]:
    """Return the canonical GCS locations that back the review workflow."""
    return {
        "bucket": utipy.config.BUCKET_NAME,
        "transcripts_prefix": TRANSCRIPTS_PREFIX,
        "review_index_blob": REVIEW_INDEX_BLOB,
        "sessions_to_train_blob": SESSIONS_TO_TRAIN_BLOB,
    }


def normalize_evaluation(value: Any) -> str:
    """Normalize one review label to the canonical wire value."""
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered == "use":
        return "Use"
    if lowered == "alter":
        return "Alter"
    if lowered == "reject":
        return "Reject"
    raise ValueError(f"Unsupported evaluation value: {value!r}")


def normalize_reviewer(value: Any) -> str:
    """Normalize one reviewer code."""
    text = str(value or "").strip().upper()
    if text in REVIEWERS:
        return text
    raise ValueError(f"Unsupported reviewer value: {value!r}")


def review_field_name(reviewer: str) -> str:
    """Return the manifest field that stores one reviewer's decision."""
    reviewer_code = normalize_reviewer(reviewer)
    if reviewer_code == "ZB":
        return "review_zb"
    return "review_cm"


def derive_final_evaluation(review_zb: Any, review_cm: Any) -> str:
    """Apply the two-reviewer rule set to one session record."""
    zb = normalize_evaluation(review_zb)
    cm = normalize_evaluation(review_cm)

    if zb == "Use" and cm == "Use":
        return "Use"
    if zb == "Reject" and cm == "Reject":
        return "Reject"
    if "Alter" in {zb, cm}:
        return "Alter"
    if zb and cm and zb != cm:
        return "Alter"
    return ""


def _require_bucket():
    """Return the configured GCS bucket or fail explicitly."""
    if not utipy.BUCKET:
        raise RuntimeError(
            "Cloud Storage bucket client unavailable; session review storage requires GCS."
        )
    return utipy.BUCKET


def _blob_exists(blob_name: str) -> bool:
    """Return whether one named blob currently exists."""
    return bool(_require_bucket().blob(blob_name).exists())


def _read_jsonl_blob(blob_name: str) -> list[dict[str, Any]]:
    """Read a JSONL blob into a list of JSON objects."""
    if not _blob_exists(blob_name):
        return []

    payload = _require_bucket().blob(blob_name).download_as_text()
    rows: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(payload.splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            row = json.loads(raw_line)
        except Exception as exc:
            raise ValueError(
                f"{blob_name}: invalid JSON on line {line_number}: {exc}"
            ) from exc
        if not isinstance(row, dict):
            raise ValueError(f"{blob_name}: line {line_number} is not a JSON object")
        rows.append(row)
    return rows


def _write_jsonl_blob(blob_name: str, rows: list[dict[str, Any]]) -> None:
    """Write a list of JSON objects back to one JSONL blob."""
    payload = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    if payload:
        payload += "\n"
    _require_bucket().blob(blob_name).upload_from_string(
        payload,
        content_type="application/jsonl",
    )


def _is_message_obj(obj: object) -> bool:
    """Return whether a JSON object looks like a stored chat message."""
    if not isinstance(obj, dict):
        return False
    return obj.get("role") in _ROLE_SET and isinstance(obj.get("content"), str)


def validate_messages(messages: Any) -> list[dict[str, str]]:
    """Validate and normalize one stored conversation transcript."""
    if not isinstance(messages, list) or not messages:
        raise ValueError("Row is missing a non-empty messages array")

    cleaned: list[dict[str, str]] = []
    for idx, message in enumerate(messages, start=1):
        if not isinstance(message, dict):
            raise ValueError(f"Message {idx} is not an object")
        role = message.get("role")
        content = message.get("content")
        if role not in _ROLE_SET:
            raise ValueError(f"Message {idx} has unsupported role {role!r}")
        if not isinstance(content, str):
            raise ValueError(f"Message {idx} content is not a string")
        cleaned.append({"role": role, "content": content})
    return cleaned


def _transcript_hash(messages: list[dict[str, str]]) -> str:
    """Return a stable hash for one transcript payload."""
    payload = json.dumps(messages, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _truncate_one_line(text: str, limit: int = 180) -> str:
    """Compact and trim one preview string."""
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)] + "..."


def _pick_preview(messages: list[dict[str, str]]) -> tuple[str, str]:
    """Extract one short user and assistant preview from a transcript."""
    user = next(
        (
            message["content"]
            for message in messages
            if message["role"] == "user" and message["content"].strip()
        ),
        "",
    )
    assistant = next(
        (
            message["content"]
            for message in messages
            if message["role"] == "assistant" and message["content"].strip()
        ),
        "",
    )
    return _truncate_one_line(user), _truncate_one_line(assistant)


def _normalize_string(value: Any) -> str:
    """Normalize a scalar metadata value to a trimmed string."""
    return str(value or "").strip()


def _normalize_review_row(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize one manifest row into the current cloud-backed schema."""
    normalized = copy.deepcopy(row)
    conversation_id = _normalize_string(normalized.get("conversation_id"))
    transcript_blob_name = _normalize_string(normalized.get("transcript_blob_name"))
    if not transcript_blob_name and conversation_id:
        transcript_blob_name = f"{TRANSCRIPTS_PREFIX}{conversation_id}.jsonl"
    if not conversation_id and transcript_blob_name:
        conversation_id = Path(transcript_blob_name).stem
    if not conversation_id:
        raise ValueError("Review row is missing conversation_id")
    if not transcript_blob_name:
        raise ValueError("Review row is missing transcript_blob_name")

    review_zb = normalize_evaluation(normalized.get("review_zb", ""))
    review_cm = normalize_evaluation(normalized.get("review_cm", ""))
    legacy_evaluation = normalize_evaluation(normalized.get("evaluation", ""))
    if review_zb or review_cm:
        review_version = max(
            DUAL_REVIEW_VERSION,
            int(normalized.get("review_version") or DUAL_REVIEW_VERSION),
        )
        evaluation = derive_final_evaluation(review_zb, review_cm)
    elif legacy_evaluation:
        review_version = DUAL_REVIEW_VERSION
        review_cm = legacy_evaluation
        evaluation = legacy_evaluation
    else:
        review_version = DUAL_REVIEW_VERSION
        evaluation = ""

    normalized["conversation_id"] = conversation_id
    normalized["transcript_blob_name"] = transcript_blob_name
    normalized["transcript_hash"] = _normalize_string(normalized.get("transcript_hash"))
    normalized["message_count"] = int(normalized.get("message_count") or 0)
    normalized["preview_user"] = _normalize_string(normalized.get("preview_user"))
    normalized["preview_assistant"] = _normalize_string(
        normalized.get("preview_assistant")
    )
    normalized["review_version"] = review_version
    normalized["review_zb"] = review_zb
    normalized["review_cm"] = review_cm
    normalized["evaluation"] = evaluation
    normalized["student"] = _normalize_string(normalized.get("student"))
    normalized["model"] = _normalize_string(normalized.get("model"))
    normalized["case_id"] = _normalize_string(normalized.get("case_id"))
    normalized["profile"] = _normalize_string(normalized.get("profile"))
    normalized["saved_at"] = _normalize_string(normalized.get("saved_at"))
    normalized["loss"] = normalized.get("loss", "")
    return normalized


def _sort_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return rows in deterministic newest-first review order."""
    return sorted(
        rows,
        key=lambda row: (
            _normalize_string(row.get("saved_at")),
            _normalize_string(row.get("conversation_id")),
        ),
        reverse=True,
    )


def _load_transcript_blob(blob_name: str) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Load one archived transcript blob into metadata and validated messages."""
    rows = _read_jsonl_blob(blob_name)
    metadata: dict[str, Any] = {}
    messages: list[dict[str, str]] = []
    for row in rows:
        if _is_message_obj(row):
            messages.append({"role": row["role"], "content": row["content"]})
        elif not metadata:
            metadata = dict(row)
    cleaned_messages = validate_messages(messages)
    return metadata, cleaned_messages


def _manifest_row_from_transcript(
    transcript_blob_name: str,
    transcript_metadata: dict[str, Any],
    messages: list[dict[str, str]],
    previous_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one normalized manifest row from an archived transcript."""
    conversation_id = (
        _normalize_string(transcript_metadata.get("conversation_id"))
        or Path(transcript_blob_name).stem
    )
    preview_user, preview_assistant = _pick_preview(messages)
    base: dict[str, Any] = {
        "conversation_id": conversation_id,
        "transcript_blob_name": transcript_blob_name,
        "transcript_hash": _transcript_hash(messages),
        "message_count": len(messages),
        "preview_user": preview_user,
        "preview_assistant": preview_assistant,
        "student": _normalize_string(transcript_metadata.get("student")),
        "model": _normalize_string(
            transcript_metadata.get("model") or transcript_metadata.get("model_name")
        ),
        "case_id": _normalize_string(transcript_metadata.get("case_id")),
        "profile": _normalize_string(transcript_metadata.get("profile")),
        "saved_at": _normalize_string(transcript_metadata.get("saved_at")),
        "loss": transcript_metadata.get("loss", transcript_metadata.get("training_loss", "")),
        "review_version": DUAL_REVIEW_VERSION,
        "review_zb": "",
        "review_cm": "",
        "evaluation": "",
    }
    if previous_state:
        base.update(
            {
                "review_version": previous_state.get(
                    "review_version", DUAL_REVIEW_VERSION
                ),
                "review_zb": previous_state.get("review_zb", ""),
                "review_cm": previous_state.get("review_cm", ""),
                "evaluation": previous_state.get("evaluation", ""),
            }
        )
    return _normalize_review_row(base)


def build_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Count review states across the full manifest."""
    summary = {"total": len(rows), "Use": 0, "Alter": 0, "Reject": 0, "unreviewed": 0}
    for row in rows:
        evaluation = normalize_evaluation(row.get("evaluation", ""))
        if evaluation in REVIEW_CHOICES:
            summary[evaluation] += 1
        else:
            summary["unreviewed"] += 1
    return summary


def load_review_state() -> list[dict[str, Any]]:
    """Load and normalize the cloud-backed review manifest."""
    rows = [_normalize_review_row(row) for row in _read_jsonl_blob(REVIEW_INDEX_BLOB)]
    return _sort_rows(rows)


def _build_sessions_to_train(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build the training export from manifest rows tagged Use."""
    train_rows: list[dict[str, Any]] = []
    for row in rows:
        if normalize_evaluation(row.get("evaluation", "")) != "Use":
            continue
        _metadata, messages = _load_transcript_blob(row["transcript_blob_name"])
        train_rows.append({"messages": messages})
    return train_rows


def save_review_state(rows: list[dict[str, Any]]) -> None:
    """Write the manifest and the derived training export back to GCS."""
    normalized_rows = _sort_rows([_normalize_review_row(row) for row in rows])
    _write_jsonl_blob(REVIEW_INDEX_BLOB, normalized_rows)
    _write_jsonl_blob(SESSIONS_TO_TRAIN_BLOB, _build_sessions_to_train(normalized_rows))


def apply_reviewer_decision(
    row: dict[str, Any], reviewer: str, evaluation: str
) -> dict[str, Any]:
    """Apply one reviewer decision to one manifest row."""
    updated = _normalize_review_row(row)
    updated["review_version"] = DUAL_REVIEW_VERSION
    updated[review_field_name(reviewer)] = normalize_evaluation(evaluation)
    updated["evaluation"] = derive_final_evaluation(
        updated.get("review_zb", ""), updated.get("review_cm", "")
    )
    return updated


def first_unreviewed_index(rows: list[dict[str, Any]]) -> int | None:
    """Return the first unreviewed index in the current manifest."""
    for index, row in enumerate(rows):
        if normalize_evaluation(row.get("evaluation", "")) == "":
            return index
    return None


def next_index(rows: list[dict[str, Any]], current: int) -> int:
    """Return the next valid review index."""
    if current + 1 < len(rows):
        return current + 1
    return current


def previous_index(current: int) -> int:
    """Return the previous valid review index."""
    return max(0, current - 1)


def next_unreviewed_after(rows: list[dict[str, Any]], current: int) -> int | None:
    """Return the next unreviewed index after the current record."""
    for index in range(current + 1, len(rows)):
        if normalize_evaluation(rows[index].get("evaluation", "")) == "":
            return index
    return first_unreviewed_index(rows)


def find_next_matching_index(
    rows: list[dict[str, Any]], after: int, desired: str
) -> int | None:
    """Find the next record matching one requested evaluation state."""
    def matches(row: dict[str, Any]) -> bool:
        evaluation = normalize_evaluation(row.get("evaluation", ""))
        if desired == "all":
            return True
        if desired == "unreviewed":
            return evaluation == ""
        return evaluation == desired

    start = max(-1, after)
    for index in range(start + 1, len(rows)):
        if matches(rows[index]):
            return index
    return None


def serialize_record(rows: list[dict[str, Any]], index: int) -> dict[str, Any]:
    """Serialize one review record for the admin UI or GPT action API."""
    if index < 0 or index >= len(rows):
        raise IndexError(index)

    row = _normalize_review_row(rows[index])
    transcript_metadata, messages = _load_transcript_blob(row["transcript_blob_name"])
    metadata = {
        "conversation_id": row["conversation_id"],
        "transcript_blob_name": row["transcript_blob_name"],
        "transcript_hash": row["transcript_hash"],
        "message_count": row["message_count"] or len(messages),
        "student": row["student"] or _normalize_string(transcript_metadata.get("student")),
        "model": row["model"] or _normalize_string(transcript_metadata.get("model")),
        "case_id": row["case_id"] or _normalize_string(transcript_metadata.get("case_id")),
        "profile": row["profile"] or _normalize_string(transcript_metadata.get("profile")),
        "saved_at": row["saved_at"] or _normalize_string(transcript_metadata.get("saved_at")),
        "loss": row["loss"] if row["loss"] != "" else transcript_metadata.get("loss", ""),
    }
    return {
        "index": index,
        "display_number": index + 1,
        "evaluation": row["evaluation"],
        "review_version": row["review_version"],
        "review_zb": row["review_zb"] or None,
        "review_cm": row["review_cm"] or None,
        "preview_user": row["preview_user"],
        "preview_assistant": row["preview_assistant"],
        "messages": messages,
        "metadata": metadata,
        "summary": build_summary(rows),
        "navigation": {
            "previous_index": previous_index(index),
            "next_index": next_index(rows, index),
            "next_unreviewed_index": next_unreviewed_after(rows, index),
        },
    }


def list_dashboard_rows(rows: list[dict[str, Any]], evaluation_filter: str) -> list[dict[str, Any]]:
    """Return the rows that should appear in one filtered dashboard view."""
    results: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        normalized = _normalize_review_row(row)
        evaluation = normalized["evaluation"]
        if evaluation_filter == "all":
            matches = True
        elif evaluation_filter == "unreviewed":
            matches = evaluation == ""
        else:
            matches = evaluation == evaluation_filter
        if not matches:
            continue
        results.append(
            {
                "index": index,
                "conversation_id": normalized["conversation_id"],
                "preview_user": normalized["preview_user"],
                "preview_assistant": normalized["preview_assistant"],
                "review_zb": normalized["review_zb"] or None,
                "review_cm": normalized["review_cm"] or None,
                "evaluation": evaluation or "Unreviewed",
                "saved_at": normalized["saved_at"],
            }
        )
    return results


def upsert_review_record_from_session(
    conversation_id: str,
    messages: list[dict[str, str]],
    transcript_metadata: dict[str, Any],
) -> dict[str, Any]:
    """Upsert one manifest row immediately after a session is archived."""
    cleaned_messages = validate_messages(messages)
    rows = load_review_state()
    by_conversation_id = {row["conversation_id"]: row for row in rows}
    transcript_blob_name = f"{TRANSCRIPTS_PREFIX}{conversation_id}.jsonl"
    updated = _manifest_row_from_transcript(
        transcript_blob_name,
        {**transcript_metadata, "conversation_id": conversation_id},
        cleaned_messages,
        previous_state=by_conversation_id.get(conversation_id),
    )

    kept_rows = [row for row in rows if row["conversation_id"] != conversation_id]
    kept_rows.append(updated)
    save_review_state(kept_rows)
    return updated


def _legacy_reviewed_jsonl_path(repo_root: Path) -> Path:
    """Return the legacy generated reviewed JSONL path used for rollout backfill."""
    return (
        repo_root / "training" / "generated" / "collected_sessions_with_evaluations.jsonl"
    ).resolve()


def _load_legacy_review_state_by_hash(repo_root: Path) -> dict[str, dict[str, Any]]:
    """Load prior local review decisions keyed by transcript hash."""
    if not utipy.config.LOCAL:
        return {}

    legacy_path = _legacy_reviewed_jsonl_path(repo_root)
    if not legacy_path.exists():
        return {}

    states: dict[str, dict[str, Any]] = {}
    for raw_line in legacy_path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        try:
            row = json.loads(raw_line)
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        try:
            messages = validate_messages(row.get("messages"))
            normalized = _normalize_review_row(
                {
                    "conversation_id": _normalize_string(
                        row.get("metadata", {}).get("conversation_id")
                    )
                    or _normalize_string(row.get("conversation_id"))
                    or _transcript_hash(messages),
                    "transcript_blob_name": "",
                    "transcript_hash": _transcript_hash(messages),
                    "review_version": row.get("review_version", DUAL_REVIEW_VERSION),
                    "review_zb": row.get("review_zb", ""),
                    "review_cm": row.get("review_cm", ""),
                    "evaluation": row.get("evaluation", ""),
                }
            )
        except Exception:
            continue
        if not (
            normalized["evaluation"]
            or normalized["review_zb"]
            or normalized["review_cm"]
        ):
            continue
        states.setdefault(
            normalized["transcript_hash"],
            {
                "review_version": normalized["review_version"],
                "review_zb": normalized["review_zb"],
                "review_cm": normalized["review_cm"],
                "evaluation": normalized["evaluation"],
            },
        )
    return states


def backfill_review_state(repo_root: Path) -> dict[str, int | str]:
    """Rebuild the review manifest from archived transcripts without deleting data."""
    rows = load_review_state()
    current_by_id = {row["conversation_id"]: row for row in rows}
    legacy_by_hash = _load_legacy_review_state_by_hash(repo_root.resolve())

    rebuilt_rows: list[dict[str, Any]] = []
    preserved_current = 0
    preserved_legacy = 0
    transcript_blobs = sorted(
        blob.name
        for blob in _require_bucket().list_blobs(prefix=TRANSCRIPTS_PREFIX)
        if blob.name.endswith(".jsonl")
    )

    for blob_name in transcript_blobs:
        transcript_metadata, messages = _load_transcript_blob(blob_name)
        transcript_hash = _transcript_hash(messages)
        conversation_id = (
            _normalize_string(transcript_metadata.get("conversation_id"))
            or Path(blob_name).stem
        )
        previous_state = current_by_id.get(conversation_id)
        if previous_state and (
            previous_state.get("evaluation")
            or previous_state.get("review_zb")
            or previous_state.get("review_cm")
        ):
            preserved_current += 1
        if previous_state is None:
            previous_state = legacy_by_hash.get(transcript_hash)
            if previous_state:
                preserved_legacy += 1

        rebuilt_rows.append(
            _manifest_row_from_transcript(
                blob_name,
                transcript_metadata,
                messages,
                previous_state=previous_state,
            )
        )

    save_review_state(rebuilt_rows)
    train_rows = _build_sessions_to_train(rebuilt_rows)
    return {
        "status": "backfilled",
        "transcripts_scanned": len(transcript_blobs),
        "review_rows": len(rebuilt_rows),
        "preserved_existing_reviews": preserved_current,
        "preserved_legacy_reviews": preserved_legacy,
        "sessions_to_train_rows": len(train_rows),
        **storage_metadata(),
    }
