"""Review navigation, decisions, requests, and runtime status reporting."""

import logging
from typing import Any

import session_reviews as reviews
import utilities as utipy
import storage_policy
from .errors import ServiceError, require_archive_storage


def load_rows() -> list[dict[str, Any]]:
    """Require and load the existing cloud review manifest."""
    require_archive_storage("Review storage is unavailable or unconfigured.")
    return reviews.load_review_state()


def summary() -> dict[str, Any]:
    """Return review progress and storage metadata."""
    rows = load_rows()
    return {
        "summary": reviews.build_summary(rows),
        "progress_summary": reviews.build_progress_summary(rows),
        "storage": reviews.storage_metadata(),
    }


def require_index(rows: list[dict[str, Any]], index: int) -> None:
    """Reject indices that no longer address a review record."""
    if index < 0 or index >= len(rows):
        raise ServiceError(
            "review_record_not_found", "Record index out of range.", 404, index=index
        )


def record(index: int) -> dict[str, Any]:
    """Retrieve a full review record."""
    rows = load_rows()
    require_index(rows, index)
    return {"record": reviews.serialize_record(rows, index)}


def needs_review(offset: int = 0, limit: int = 25) -> dict[str, Any]:
    """Page compact summaries awaiting Zenbot review."""
    rows = load_rows()
    indices = reviews.indices_needing_reviewer(rows, "ZB")
    return {
        "reviewer": "ZB",
        "total_matching": len(indices),
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < len(indices),
        "records": reviews.serialize_review_list_items(
            rows, indices[offset : offset + limit]
        ),
    }


def random_review(exclude_indices: set[int] | None = None) -> dict[str, Any]:
    """Pick one full record still awaiting Zenbot review."""
    rows = load_rows()
    excluded = exclude_indices or set()
    candidates = reviews.indices_needing_reviewer(rows, "ZB")
    selected = reviews.random_review_indices(
        rows, 1, candidate_indices=candidates, exclude_indices=excluded
    )
    index = selected[0] if selected else None
    return {
        "reviewer": "ZB",
        "available_count": len([i for i in candidates if i not in excluded]),
        "selected_index": index,
        "record": reviews.serialize_record(rows, index) if index is not None else None,
    }


def sample(count: int = 3, exclude_indices: set[int] | None = None) -> dict[str, Any]:
    """Return compact summaries for a unique random sample."""
    rows = load_rows()
    excluded = exclude_indices or set()
    indices = reviews.random_review_indices(rows, count, exclude_indices=excluded)
    return {
        "requested_count": count,
        "sampled_count": len(indices),
        "available_count": len([i for i in range(len(rows)) if i not in excluded]),
        "records": reviews.serialize_review_list_items(rows, indices),
    }


def next_record(
    evaluation: str = "unreviewed",
    after: int = -1,
    *,
    rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Find the next full record matching the requested review state."""
    rows = load_rows() if rows is None else rows
    index = reviews.find_next_matching_index(rows, after, evaluation)
    return {
        "evaluation_filter": evaluation,
        "next_index": index,
        "record": reviews.serialize_record(rows, index) if index is not None else None,
    }


def decision(
    index: int,
    evaluation: str,
    reviewer: str = "ZB",
    conversation_id: str = "",
    *,
    rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Apply one review decision, guarding against a stale record index."""
    rows = load_rows() if rows is None else rows
    require_index(rows, index)
    actual_id = str(rows[index].get("conversation_id", "")).strip()
    if conversation_id and conversation_id != actual_id:
        raise ServiceError(
            "review_record_mismatch",
            "Decision payload conversation_id does not match the current record at this index. Refetch the review record before saving.",
            409,
            index=index,
            expected_conversation_id=conversation_id,
            conversation_id=actual_id,
        )
    try:
        evaluation = reviews.normalize_evaluation(evaluation)
    except ValueError as exc:
        raise ServiceError("bad_request", str(exc), index=index) from exc
    if evaluation not in reviews.REVIEW_CHOICES:
        raise ServiceError(
            "bad_request", "evaluation must be Use, Alter, or Reject.", index=index
        )
    try:
        reviewer = reviews.normalize_reviewer(reviewer)
    except ValueError as exc:
        raise ServiceError("bad_request", str(exc), index=index) from exc
    rows[index] = reviews.apply_reviewer_decision(rows[index], reviewer, evaluation)
    reviews.save_review_state(rows)
    return {
        "record": reviews.serialize_record(rows, index),
        "next_unreviewed_index": reviews.next_unreviewed_after(rows, index),
        "summary": reviews.build_summary(rows),
        "progress_summary": reviews.build_progress_summary(rows),
    }


def enqueue(
    conversation_id: str, reviewer: str = "CM", note: str = ""
) -> dict[str, Any]:
    """Queue a follow-up review using the existing JSONL writer."""
    payload = {
        "queued_at": utipy._utc_now(),
        "conversation_id": conversation_id,
        "reviewer": reviewer,
        "note": note,
    }
    utipy._write_jsonl_record(
        utipy.config.REVIEW_REQUESTS_BLOB, payload, utipy._LOCAL_REVIEW_REQUESTS_PATH
    )
    return {"message": "review request queued", **payload}


def report_status(stage: str, detail: str = "") -> dict[str, Any]:
    """Record an operational breadcrumb and return its accepted fields."""
    if not storage_policy.is_strict():
        logging.info("runtime_status_event stage=%s detail=%s", stage, detail)
    return {
        "message": "runtime status event reported",
        "stage": stage,
        "detail": detail,
    }
