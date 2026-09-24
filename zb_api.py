"""Authenticated JSON endpoints consumed by GPT Actions and operator tools."""

from __future__ import annotations

import logging

from flask import Blueprint, request
from pydantic import ValidationError

from app_support import (
    ChatStartRequest,
    ChatTurnRequest,
    _api_archive_storage_guard,
    _api_failure,
    _api_storage_unavailable,
    _api_success,
    _load_review_state_for_api,
    _normalize_review_filter,
    _parse_bool_query_arg,
    _parse_bounded_int_arg,
    _parse_exclude_indices_arg,
    _parse_int_query_arg,
    _require_admin_auth,
    _settings_locked_response,
)
from contracts import (
    EnqueueReviewArgs,
    ReportUiStatusArgs,
    SaveMemoryCandidateArgs,
    SearchExemplarsArgs,
)
from zb_services import ServiceError, conversations, koans, memories, reviews
import utilities as utipy
from utilities import SessionSettingsLockedError

zb_api_bp = Blueprint("zb_api", __name__)


def _workflow(function, *args, _error_context=None, **kwargs):
    """Translate transport-independent workflow results and known application errors."""
    try:
        return _api_success(**function(*args, **kwargs))
    except ServiceError as exc:
        return _api_failure(
            exc.code,
            str(exc),
            exc.status_code,
            **{**(_error_context or {}), **exc.details},
        )


@zb_api_bp.route("/zb_api/chat/options", methods=["GET"])
def zb_api_chat_options():
    """Return the canonical authenticated session-settings options payload."""

    return _workflow(conversations.options)


@zb_api_bp.route("/zb_api/chat", methods=["POST"])
def zb_api_chat():
    """Process one authenticated API chat turn without browser cookies."""
    conversation_id = ""
    try:
        data = utipy.get_request_data()
        payload = ChatTurnRequest.from_dict(data)
        conversation_id = payload.conversation_id.strip()
        return _workflow(conversations.chat, **vars(payload))
    except SessionSettingsLockedError as e:
        return _settings_locked_response(e, api=True, conversation_id=conversation_id)
    except ValueError as e:
        return _api_failure(
            "bad_request",
            str(e),
            400,
            conversation_id=conversation_id or None,
        )
    except Exception:
        logging.exception("/zb_api/chat failed")
        return _api_failure(
            "internal_server_error",
            "Unexpected server-side failure while processing the turn.",
            500,
            conversation_id=conversation_id or None,
        )


@zb_api_bp.route("/zb_api/chat_case/<case_id>", methods=["POST"])
def zb_api_chat_case(case_id: str):
    """Start an authenticated API conversation anchored to one koan case."""
    conversation_id = ""
    try:
        data = utipy.get_request_data()
        payload = ChatStartRequest.from_dict(data, default_student="api-case")

        return _workflow(
            conversations.start_case,
            case_id,
            student=payload.student,
            settings=payload.settings,
        )
    except ValueError as e:
        return _api_failure(
            "bad_request",
            str(e),
            400,
            conversation_id=conversation_id or None,
            case_id=str(case_id),
        )
    except Exception:
        logging.exception("/zb_api/chat_case failed")
        return _api_failure(
            "internal_server_error",
            "Unexpected server-side failure while creating the koan conversation.",
            500,
            conversation_id=conversation_id or None,
            case_id=str(case_id),
        )


@zb_api_bp.route("/zb_api/save_chat", methods=["POST"])
def zb_api_save_chat():
    """Archive an authenticated API conversation by conversation ID."""
    try:
        storage_failure = _api_archive_storage_guard(
            "Archive storage is unavailable or unconfigured."
        )
        if storage_failure is not None:
            return storage_failure

        data = utipy.get_request_data()
        conversation_id = str(data.get("conversation_id", "")).strip()
        return _workflow(conversations.archive, conversation_id)
    except RuntimeError as e:
        return _api_storage_unavailable(
            str(e), conversation_id=locals().get("conversation_id", None)
        )
    except Exception:
        logging.exception("/zb_api/save_chat failed")
        return _api_failure(
            "internal_server_error",
            "Unexpected server-side failure while archiving the conversation.",
            500,
            conversation_id=locals().get("conversation_id", None),
        )


@zb_api_bp.route("/zb_api/conversations/list", methods=["GET"])
def zb_api_conversations_list():
    """List archived conversation identifiers and lightweight metadata."""
    storage_failure = _api_archive_storage_guard(
        "Archive storage is unavailable or unconfigured."
    )
    if storage_failure is not None:
        return storage_failure
    try:
        offset = _parse_int_query_arg("offset", 0, 0, 1_000_000)
        limit = _parse_int_query_arg("limit", 25, 1, 100)
        case_id = str(request.args.get("case_id", "")).strip()
        student = str(request.args.get("student", "")).strip().lower()
        start_date = str(request.args.get("start_date", "")).strip()
        end_date = str(request.args.get("end_date", "")).strip()
        return _workflow(
            conversations.list_archives,
            offset,
            limit,
            case_id,
            student,
            start_date,
            end_date,
        )
    except ValueError as exc:
        return _api_failure("bad_request", str(exc), 400)
    except Exception as exc:
        logging.exception("/zb_api/conversations/list failed")
        return _api_storage_unavailable(str(exc))


@zb_api_bp.route("/zb_api/conversations/<conversation_id>", methods=["GET"])
def zb_api_conversation(conversation_id: str):
    """Fetch one archived conversation transcript from Cloud Storage."""
    storage_failure = _api_archive_storage_guard(
        "Archive storage is unavailable or unconfigured."
    )
    if storage_failure is not None:
        return storage_failure

    if not conversation_id:
        return _api_failure(
            "bad_request",
            "Missing conversation_id.",
            400,
        )
    try:
        return _workflow(conversations.get_archive, conversation_id)
    except Exception as exc:
        logging.exception("/zb_api/conversations/%s failed", conversation_id)
        return _api_storage_unavailable(str(exc), conversation_id=conversation_id)


@zb_api_bp.route("/zb_api/load_memory_logbook", methods=["GET"])
def zb_api_load_memory_logbook():
    """Return a compact newest-first memory index for GPT-side selection."""
    try:
        limit = _parse_bounded_int_arg("limit", default=12, minimum=1, maximum=25)
        end_index_raw = request.args.get("end_index", "").strip()
        end_index = int(end_index_raw) if end_index_raw else None
    except ValueError as exc:
        return _api_failure("bad_request", str(exc), 400)

    try:
        return _workflow(memories.summaries, limit, end_index)
    except RuntimeError as exc:
        return _api_storage_unavailable(str(exc))
    except ValueError as exc:
        return _api_failure("bad_request", str(exc), 400)


@zb_api_bp.route("/zb_api/load_memory_entry/<serial_number>", methods=["GET"])
def zb_api_load_memory_entry(serial_number: str):
    """Fetch one canonical memory-logbook entry by serial number."""
    try:
        return _workflow(memories.get_entry, serial_number)
    except RuntimeError as exc:
        return _api_storage_unavailable(str(exc), serial_number=str(serial_number))


@zb_api_bp.route("/zb_api/exemplars/search", methods=["GET"])
def zb_api_search_exemplars():
    """Search accepted review sessions for compact exemplar snippets."""
    try:
        args = SearchExemplarsArgs.model_validate(
            {
                "query": str(request.args.get("query", "")).strip(),
                "limit": _parse_int_query_arg("limit", 3, 1, 5),
                "case_id": str(request.args.get("case_id", "")).strip(),
            }
        )
    except ValueError as exc:
        return _api_failure("bad_request", str(exc), 400)
    except ValidationError as exc:
        return _api_failure(
            "bad_request",
            "Exemplar search query failed validation.",
            400,
            details=exc.errors(),
        )

    try:
        return _workflow(koans.search_exemplars, **args.model_dump())
    except RuntimeError as exc:
        return _api_storage_unavailable(str(exc))
    except Exception:
        logging.exception("/zb_api/exemplars/search failed")
        return _api_failure(
            "internal_server_error",
            "Unexpected server-side failure while searching exemplar sessions.",
            500,
        )


@zb_api_bp.route("/zb_api/memory/candidates", methods=["POST"])
def zb_api_queue_memory_candidate():
    """Queue one structured memory candidate for later human/operator review."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _api_failure("bad_request", "JSON body is required.", 400)

    try:
        args = SaveMemoryCandidateArgs.model_validate(data)
        return _workflow(memories.candidate, args.entry)
    except ValidationError as exc:
        return _api_failure(
            "bad_request",
            "Memory candidate payload failed validation.",
            400,
            details=exc.errors(),
        )
    except RuntimeError as exc:
        return _api_storage_unavailable(str(exc))
    except Exception:
        logging.exception("/zb_api/memory/candidates failed")
        return _api_failure(
            "internal_server_error",
            "Unexpected server-side failure while queueing the memory candidate.",
            500,
        )


@zb_api_bp.route("/zb_api/update_memory_logbook", methods=["POST"])
def zb_api_update_memory_logbook():
    """Append one entry or replace the full memory logbook via the new API."""
    data = request.get_json(silent=True)
    if not data:
        return _api_failure("bad_request", "Invalid or missing JSON body.", 400)

    if "entry" in data and "full_logbook" in data:
        return _api_failure(
            "bad_request",
            "JSON body must contain either 'entry' or 'full_logbook', not both.",
            400,
        )

    if "entry" in data:
        entry = data["entry"]
        if not isinstance(entry, dict):
            return _api_failure("bad_request", "'entry' must be an object.", 400)
        try:
            return _workflow(memories.commit, entry)
        except RuntimeError as e:
            return _api_storage_unavailable(str(e))
        except Exception:
            return _api_failure(
                "internal_server_error",
                "Unexpected server-side failure while updating the memory logbook.",
                500,
            )

    if "full_logbook" in data:
        full = data["full_logbook"]
        if not isinstance(full, list):
            return _api_failure(
                "bad_request",
                "'full_logbook' must be an array.",
                400,
            )
        try:
            return _workflow(memories.replace, full)
        except RuntimeError as e:
            return _api_storage_unavailable(str(e))
        except Exception:
            return _api_failure(
                "internal_server_error",
                "Unexpected server-side failure while replacing the memory logbook.",
                500,
            )

    if isinstance(data, dict):
        try:
            return _workflow(memories.commit, data, legacy=True)
        except RuntimeError as e:
            return _api_storage_unavailable(str(e))
        except Exception:
            return _api_failure(
                "internal_server_error",
                "Unexpected server-side failure while updating the memory logbook.",
                500,
            )

    return _api_failure(
        "bad_request",
        "JSON body must contain either 'entry' or 'full_logbook'.",
        400,
    )


@zb_api_bp.route("/zb_api/get_random_koan", methods=["GET"])
def zb_api_get_random_koan():
    """Return one random koan case as a flattened JSON payload."""
    return _workflow(koans.random_koan)


@zb_api_bp.route("/zb_api/koans/by-title", methods=["GET"])
def zb_api_get_koan_by_title():
    """Return one koan by exact title, or by unambiguous title substring."""
    return _workflow(koans.by_title, str(request.args.get("title", "")).strip())


@zb_api_bp.route("/zb_api/koans/<case_id>", methods=["GET"])
def zb_api_get_koan_by_id(case_id: str):
    """Return one koan case by ID."""
    try:
        include_solution_notes = _parse_bool_query_arg("include_solution_notes")
    except ValueError as exc:
        return _api_failure("bad_request", str(exc), 400, case_id=str(case_id))

    return _workflow(koans.by_id, case_id, include_solution_notes)


@zb_api_bp.get("/zb_api/session-evaluations/summary")
def api_session_evaluations_summary():
    """Return review counts plus the active cloud storage locations."""
    _require_admin_auth()
    try:
        return _workflow(reviews.summary)
    except RuntimeError as exc:
        return _api_storage_unavailable(str(exc))
    except Exception:
        logging.exception("/zb_api/session-evaluations/summary failed")
        return _api_failure(
            "internal_server_error",
            "Unexpected server-side failure while loading review summary.",
            500,
        )


@zb_api_bp.get("/zb_api/session-evaluations/record/<int:index>")
def api_session_evaluations_record(index: int):
    """Fetch one review record from the cloud-backed review manifest."""
    _require_admin_auth()
    try:
        return _workflow(reviews.record, index, _error_context={"index": index})
    except RuntimeError as exc:
        return _api_storage_unavailable(str(exc), index=index)
    except Exception:
        logging.exception("/zb_api/session-evaluations/record/%s failed", index)
        return _api_failure(
            "internal_server_error",
            "Unexpected server-side failure while loading the review record.",
            500,
            index=index,
        )


@zb_api_bp.get("/zb_api/session-evaluations/needs-zb-review")
def api_session_evaluations_needs_zb_review():
    """List compact summaries for sessions still awaiting ZB review."""
    _require_admin_auth()
    try:
        offset = _parse_int_query_arg("offset", 0, 0, 1_000_000)
        limit = _parse_int_query_arg("limit", 25, 1, 100)
        return _workflow(reviews.needs_review, offset, limit)
    except RuntimeError as exc:
        return _api_storage_unavailable(str(exc))
    except ValueError as exc:
        return _api_failure("bad_request", str(exc), 400)
    except Exception:
        logging.exception("/zb_api/session-evaluations/needs-zb-review failed")
        return _api_failure(
            "internal_server_error",
            "Unexpected server-side failure while listing ZB review records.",
            500,
        )


@zb_api_bp.get("/zb_api/session-evaluations/random-zb-review")
def api_session_evaluations_random_zb_review():
    """Return one random full record that still awaits ZB review."""
    _require_admin_auth()
    try:
        exclude_indices = _parse_exclude_indices_arg()
        return _workflow(reviews.random_review, exclude_indices)
    except RuntimeError as exc:
        return _api_storage_unavailable(str(exc))
    except ValueError as exc:
        return _api_failure("bad_request", str(exc), 400)
    except Exception:
        logging.exception("/zb_api/session-evaluations/random-zb-review failed")
        return _api_failure(
            "internal_server_error",
            "Unexpected server-side failure while selecting a ZB review record.",
            500,
        )


@zb_api_bp.post("/zb_api/session-evaluations/review-requests")
def api_session_evaluations_review_request():
    """Queue one follow-up review request for a conversation."""
    _require_admin_auth()
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _api_failure("bad_request", "JSON body is required.", 400)

    try:
        args = EnqueueReviewArgs.model_validate(data)
        return _workflow(reviews.enqueue, **args.model_dump())
    except ValidationError as exc:
        return _api_failure(
            "bad_request",
            "Review request payload failed validation.",
            400,
            details=exc.errors(),
        )
    except RuntimeError as exc:
        return _api_storage_unavailable(str(exc))
    except Exception:
        logging.exception("/zb_api/session-evaluations/review-requests failed")
        return _api_failure(
            "internal_server_error",
            "Unexpected server-side failure while queueing the review request.",
            500,
        )


@zb_api_bp.post("/zb_api/session-evaluations/record/<int:index>/decision")
def api_session_evaluations_decision(index: int):
    """Apply a JSON review decision for the requested reviewer and record."""
    _require_admin_auth()
    try:
        rows = _load_review_state_for_api()
    except RuntimeError as exc:
        return _api_storage_unavailable(str(exc), index=index)
    except Exception:
        logging.exception(
            "/zb_api/session-evaluations/record/%s/decision failed", index
        )
        return _api_failure(
            "internal_server_error",
            "Unexpected server-side failure while loading the review manifest.",
            500,
            index=index,
        )

    if index < 0 or index >= len(rows):
        return _api_failure(
            "review_record_not_found",
            "Record index out of range.",
            404,
            index=index,
        )

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _api_failure("bad_request", "JSON body is required.", 400, index=index)

    try:
        return _workflow(
            reviews.decision,
            index,
            data.get("evaluation", ""),
            data.get("reviewer", "ZB"),
            str(data.get("conversation_id", "")).strip(),
            rows=rows,
            _error_context={"index": index},
        )
    except RuntimeError as exc:
        return _api_storage_unavailable(str(exc), index=index)
    except Exception:
        logging.exception(
            "/zb_api/session-evaluations/record/%s/decision save failed", index
        )
        return _api_failure(
            "internal_server_error",
            "Unexpected server-side failure while saving the review decision.",
            500,
            index=index,
        )


@zb_api_bp.get("/zb_api/session-evaluations/random-sample")
def api_session_evaluations_random_sample():
    """Return compact summaries for a random unique sample of review rows."""
    _require_admin_auth()
    try:
        count = _parse_int_query_arg("count", 3, 1, 25)
        exclude_indices = _parse_exclude_indices_arg()
        return _workflow(reviews.sample, count, exclude_indices)
    except RuntimeError as exc:
        return _api_storage_unavailable(str(exc))
    except ValueError as exc:
        return _api_failure("bad_request", str(exc), 400)
    except Exception:
        logging.exception("/zb_api/session-evaluations/random-sample failed")
        return _api_failure(
            "internal_server_error",
            "Unexpected server-side failure while sampling review records.",
            500,
        )


@zb_api_bp.get("/zb_api/session-evaluations/next")
def api_session_evaluations_next():
    """Find the next record matching a requested review state."""
    _require_admin_auth()
    try:
        rows = _load_review_state_for_api()
    except RuntimeError as exc:
        return _api_storage_unavailable(str(exc))
    except Exception:
        logging.exception("/zb_api/session-evaluations/next failed")
        return _api_failure(
            "internal_server_error",
            "Unexpected server-side failure while searching review records.",
            500,
        )

    desired = _normalize_review_filter(request.args.get("evaluation", "unreviewed"))
    after = request.args.get("after", "-1").strip()
    try:
        after_index = int(after)
    except ValueError:
        return _api_failure(
            "bad_request",
            "Query parameter 'after' must be an integer.",
            400,
        )

    return _workflow(reviews.next_record, desired, after_index, rows=rows)


@zb_api_bp.post("/zb_api/runtime/status-events")
def zb_api_runtime_status_event():
    """Record one lightweight GPT/operator status breadcrumb."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _api_failure("bad_request", "JSON body is required.", 400)

    try:
        args = ReportUiStatusArgs.model_validate(data)
    except ValidationError as exc:
        return _api_failure(
            "bad_request",
            "Runtime status payload failed validation.",
            400,
            details=exc.errors(),
        )

    return _workflow(reviews.report_status, args.stage, args.detail)
