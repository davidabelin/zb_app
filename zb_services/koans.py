"""Koan lookup and accepted-session exemplar search."""

from typing import Any

import session_reviews
import utilities as utipy
import storage_policy
from .errors import ServiceError, require_archive_storage


def random_koan() -> dict[str, Any]:
    """Select and retrieve a random koan."""
    case_id = utipy.get_random_koan_case_id()
    if not case_id:
        raise ServiceError(
            "koan_not_found", "A random koan could not be selected.", 404
        )
    koan = utipy.get_mmnk_case(case_id)
    if not koan:
        raise ServiceError(
            "koan_not_found",
            "The selected koan could not be loaded.",
            404,
            case_id=str(case_id),
        )
    return dict(koan)


def by_title(title: str) -> dict[str, Any]:
    """Look up an exact or unambiguous partial koan title."""
    if not title:
        raise ServiceError("bad_request", "Query parameter 'title' is required.")
    try:
        result = utipy.get_mmnk_case_by_title(title)
    except ValueError as exc:
        raise ServiceError(
            "ambiguous_koan_title",
            str(exc),
            candidates=utipy.find_mmnk_case_title_candidates(title),
        ) from exc
    if not result:
        raise ServiceError(
            "koan_not_found", "No koan case matched that title.", 404, title=title
        )
    return dict(result)


def by_id(case_id: str, include_solution_notes: bool = False) -> dict[str, Any]:
    """Load a koan, optionally including the existing solution notes."""
    koan = utipy.get_mmnk_case(case_id)
    if not koan:
        raise ServiceError(
            "koan_not_found", "Koan case not found.", 404, case_id=str(case_id)
        )
    payload = dict(koan)
    if include_solution_notes:
        notes = utipy._load_solution_notes(str(payload.get("id", case_id)))
        if notes:
            payload["solution_notes"] = notes
    return payload


def search_exemplars(query: str, limit: int = 3, case_id: str = "") -> dict[str, Any]:
    """Rank accepted review sessions using the existing API matching algorithm."""
    tokens = [token for token in query.lower().split() if len(token) > 2]
    result: dict[str, Any] = {
        "query": query,
        "case_id": case_id,
        "search_status": "success",
        "returned_count": 0,
        "results": [],
    }
    if not tokens:
        result["search_status"] = "empty_query"
        return result
    require_archive_storage("Review storage is unavailable or unconfigured.")
    rows = session_reviews.load_review_state()
    ranked = []
    for index, row in enumerate(rows):
        if str(row.get("evaluation", "")).strip() != "Use":
            continue
        if case_id and str(row.get("case_id", "")).strip() != case_id:
            continue
        haystack = " ".join(
            str(row.get(key, ""))
            for key in ("preview_user", "preview_assistant", "case_id")
        ).lower()
        score = sum(1 for token in tokens if token in haystack)
        if score <= 0:
            continue
        try:
            record = session_reviews.serialize_record(rows, index)
        except Exception:
            if storage_policy.is_strict():
                raise
            continue
        ranked.append(
            (
                score,
                {
                    "conversation_id": record["metadata"]["conversation_id"],
                    "case_id": record["metadata"].get("case_id", ""),
                    "score": score,
                    "preview_user": record.get("preview_user", ""),
                    "preview_assistant": record.get("preview_assistant", ""),
                    "excerpt": utipy._excerpt_messages(record.get("messages", [])),
                },
            )
        )
    ranked.sort(key=lambda item: (-item[0], item[1]["conversation_id"]))
    result["results"] = [item[1] for item in ranked[:limit]]
    result["returned_count"] = len(result["results"])
    return result
