"""Authenticated JSON endpoints consumed by GPT Actions and operator tools."""

from __future__ import annotations

import logging
from typing import Any

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
    _memory_mutation_response,
    _normalize_review_filter,
    _parse_bool_query_arg,
    _parse_bounded_int_arg,
    _parse_exclude_indices_arg,
    _parse_int_query_arg,
    _require_admin_auth,
    _resolve_api_chat_session,
    _settings_locked_response,
    _utc_now,
)
from contracts import (
    EnqueueReviewArgs,
    ReportUiStatusArgs,
    SaveMemoryCandidateArgs,
    SearchExemplarsArgs,
)
import session_reviews
import utilities as utipy
from utilities import SessionSettingsLockedError

zb_api_bp = Blueprint("zb_api", __name__)


@zb_api_bp.route("/zb_api/chat/options", methods=["GET"])
def zb_api_chat_options():
    """Return the canonical authenticated session-settings options payload."""

    return _api_success(**utipy.session_options_payload())


@zb_api_bp.route("/zb_api/chat", methods=["POST"])
def zb_api_chat():
    """Process one authenticated API chat turn without browser cookies."""
    conversation_id = ""
    try:
        data = utipy.get_request_data()
        payload = ChatTurnRequest.from_dict(data)
        conversation_id = payload.conversation_id.strip()
        (
            conversation_id,
            messages,
            metadata,
            conversation_status,
            requested_conversation_id,
        ) = _resolve_api_chat_session(payload)

        params = utipy.get_or_init_params(metadata)
        messages = utipy.prompt_and_reply(
            messages,
            payload.message,
            params=params,
            metadata=metadata,
            conversation_id=conversation_id,
        )
        metadata["updated_at"] = _utc_now()
        utipy.save_messages_to_firestore(conversation_id, messages, metadata=metadata)

        success_payload: dict[str, Any] = {
            "conversation_id": conversation_id,
            "conversation_status": conversation_status,
            "response": messages[-1]["content"],
            "session_settings": utipy.session_settings_from_metadata(metadata),
        }
        if requested_conversation_id:
            success_payload["requested_conversation_id"] = requested_conversation_id
        return _api_success(
            **success_payload,
        )
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

        conversation_id, _messages, _metadata = utipy.create_conversation(
            student=payload.student,
            case_id=case_id,
            settings=payload.settings,
        )
        return _api_success(
            conversation_id=conversation_id,
            case_id=str(case_id),
            session_settings=_metadata.get("session_settings", {}),
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
        if not conversation_id:
            return _api_failure(
                "bad_request",
                "No conversation_id provided.",
                400,
            )

        messages, metadata = utipy.get_conversation_state(conversation_id)
        if not messages:
            return _api_failure(
                "conversation_not_found",
                "No active conversation found for the requested conversation_id.",
                404,
                conversation_id=conversation_id,
            )

        params = {
            "conversation_id": conversation_id,
            "student": metadata.get("student", "api"),
            "case_id": metadata.get("case_id", ""),
            "model": metadata.get("model_name", ""),
            "profile": metadata.get("profile", ""),
            "loss": metadata.get("training_loss", 0.0),
            "botling_id": metadata.get("botling_id", ""),
            "settings_version": metadata.get("settings_version", ""),
            "session_settings": metadata.get("session_settings", {}),
            "saved_at": _utc_now(),
        }

        blob_name = f"{session_reviews.TRANSCRIPTS_PREFIX}{conversation_id}.jsonl"
        utipy.save_chat_to_bucket(messages, params, blob_name)
        session_reviews.upsert_review_record_from_session(
            conversation_id,
            messages,
            params,
        )
        critic_response_id = utipy.submit_background_session_critic(
            messages,
            metadata,
            conversation_id,
        )
        utipy.delete_messages_from_firestore(conversation_id)

        payload: dict[str, Any] = {
            "conversation_id": conversation_id,
            "archived": True,
        }
        if critic_response_id:
            payload["background_critic_response_id"] = critic_response_id
        return _api_success(**payload)
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
        records = utipy.list_conversation_records_in_gcs()
    except ValueError as exc:
        return _api_failure("bad_request", str(exc), 400)
    except Exception as exc:
        logging.exception("/zb_api/conversations/list failed")
        return _api_storage_unavailable(str(exc))

    filtered: list[dict[str, Any]] = []
    for record in records:
        saved_at = str(record.get("saved_at", ""))
        if case_id and str(record.get("case_id", "")) != case_id:
            continue
        if student and student not in str(record.get("student", "")).lower():
            continue
        if start_date and saved_at[:10] < start_date:
            continue
        if end_date and saved_at[:10] > end_date:
            continue
        filtered.append(record)

    page = filtered[offset : offset + limit]
    return _api_success(
        conversation_ids=[str(record["conversation_id"]) for record in page],
        records=page,
        total_matching=len(filtered),
        offset=offset,
        limit=limit,
        has_more=offset + limit < len(filtered),
    )


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
        messages = utipy.get_conversation_from_gcs(conversation_id)
    except Exception as exc:
        logging.exception("/zb_api/conversations/%s failed", conversation_id)
        return _api_storage_unavailable(str(exc), conversation_id=conversation_id)
    if not messages:
        return _api_failure(
            "conversation_not_found",
            "Archived conversation not found.",
            404,
            conversation_id=conversation_id,
        )
    return _api_success(
        conversation_id=conversation_id,
        messages=messages,
    )


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
        page = utipy.load_memory_logbook_summary_page(
            limit=limit,
            end_index=end_index,
        )
    except RuntimeError as exc:
        return _api_storage_unavailable(str(exc))
    except ValueError as exc:
        return _api_failure("bad_request", str(exc), 400)
    return _api_success(
        summaries=page["summaries"],
        returned_count=page["returned_count"],
        total_count=page["total_count"],
        limit=limit,
        start_index=page["start_index"],
        end_index=page["end_index"],
        next_end_index=page["next_end_index"],
        has_more=page["has_more"],
    )


@zb_api_bp.route("/zb_api/load_memory_entry/<serial_number>", methods=["GET"])
def zb_api_load_memory_entry(serial_number: str):
    """Fetch one canonical memory-logbook entry by serial number."""
    try:
        memory = utipy.get_memory_logbook_entry(serial_number)
    except RuntimeError as exc:
        return _api_storage_unavailable(str(exc), serial_number=str(serial_number))
    if not memory:
        return _api_failure(
            "memory_entry_not_found",
            "Memory entry not found.",
            404,
            serial_number=str(serial_number),
        )

    return _api_success(
        serial_number=str(serial_number),
        memory=memory,
    )


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

    tokens = [token for token in args.query.lower().split() if len(token) > 2]
    if not tokens:
        return _api_success(
            query=args.query,
            case_id=args.case_id,
            search_status="empty_query",
            returned_count=0,
            results=[],
        )

    try:
        rows = _load_review_state_for_api()
    except RuntimeError as exc:
        return _api_storage_unavailable(str(exc))
    except Exception:
        logging.exception("/zb_api/exemplars/search failed")
        return _api_failure(
            "internal_server_error",
            "Unexpected server-side failure while searching exemplar sessions.",
            500,
        )

    ranked: list[tuple[int, dict[str, Any]]] = []
    for index, row in enumerate(rows):
        if str(row.get("evaluation", "")).strip() != "Use":
            continue
        if args.case_id and str(row.get("case_id", "")).strip() != args.case_id:
            continue
        haystack = " ".join(
            [
                str(row.get("preview_user", "")),
                str(row.get("preview_assistant", "")),
                str(row.get("case_id", "")),
            ]
        ).lower()
        score = sum(1 for token in tokens if token in haystack)
        if score <= 0:
            continue
        try:
            record = session_reviews.serialize_record(rows, index)
        except Exception:
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
    results = [item[1] for item in ranked[: args.limit]]
    return _api_success(
        query=args.query,
        case_id=args.case_id,
        search_status="success",
        returned_count=len(results),
        results=results,
    )


@zb_api_bp.route("/zb_api/memory/candidates", methods=["POST"])
def zb_api_queue_memory_candidate():
    """Queue one structured memory candidate for later human/operator review."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _api_failure("bad_request", "JSON body is required.", 400)

    try:
        args = SaveMemoryCandidateArgs.model_validate(data)
        payload = {
            "queued_at": utipy._utc_now(),
            "entry": args.entry.model_dump(mode="json"),
        }
        utipy._write_jsonl_record(
            utipy.config.MEMORY_CANDIDATE_QUEUE,
            payload,
            utipy._LOCAL_MEMORY_CANDIDATE_PATH,
        )
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

    entry = payload["entry"]
    return _api_success(
        message=(
            "memory candidate queued for review; canonical memory logbook was not updated"
        ),
        queued_at=payload["queued_at"],
        serial_number=entry.get("serial_number", ""),
        title=entry.get("title", ""),
        canonical_logbook_updated=False,
        queue_blob=utipy.config.MEMORY_CANDIDATE_QUEUE,
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
            updated = utipy.update_logbook(entry)
            saved_entry = (
                updated[-1] if updated else utipy.normalize_memory_entry(entry)
            )
            return _memory_mutation_response(
                "entry appended via POST", updated, entry=saved_entry
            )
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
            normalized = utipy.save_logbook(full)
            return _memory_mutation_response("logbook replaced via POST", normalized)
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
            updated = utipy.update_logbook(data)
            saved_entry = updated[-1] if updated else utipy.normalize_memory_entry(data)
            return _memory_mutation_response(
                "entry appended via POST (legacy body)", updated, entry=saved_entry
            )
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
    case_id = utipy.get_random_koan_case_id()
    if not case_id:
        return _api_failure(
            "koan_not_found",
            "A random koan could not be selected.",
            404,
        )

    koan = utipy.get_mmnk_case(case_id)
    if not koan:
        return _api_failure(
            "koan_not_found",
            "The selected koan could not be loaded.",
            404,
            case_id=str(case_id),
        )

    payload = dict(koan)
    return _api_success(**payload)


@zb_api_bp.route("/zb_api/koans/by-title", methods=["GET"])
def zb_api_get_koan_by_title():
    """Return one koan by exact title, or by unambiguous title substring."""
    title = str(request.args.get("title", "")).strip()
    if not title:
        return _api_failure("bad_request", "Query parameter 'title' is required.", 400)

    try:
        result = utipy.get_mmnk_case_by_title(title)
    except ValueError as exc:
        return _api_failure(
            "ambiguous_koan_title",
            str(exc),
            400,
            candidates=utipy.find_mmnk_case_title_candidates(title),
        )
    if not result:
        return _api_failure(
            "koan_not_found",
            "No koan case matched that title.",
            404,
            title=title,
        )
    return _api_success(**dict(result))


@zb_api_bp.route("/zb_api/koans/<case_id>", methods=["GET"])
def zb_api_get_koan_by_id(case_id: str):
    """Return one koan case by ID."""
    try:
        include_solution_notes = _parse_bool_query_arg("include_solution_notes")
    except ValueError as exc:
        return _api_failure("bad_request", str(exc), 400, case_id=str(case_id))

    koan = utipy.get_mmnk_case(case_id)
    if not koan:
        return _api_failure(
            "koan_not_found",
            "Koan case not found.",
            404,
            case_id=str(case_id),
        )
    payload = dict(koan)
    if include_solution_notes:
        notes = utipy._load_solution_notes(str(payload.get("id", case_id)))
        if notes:
            payload["solution_notes"] = notes
    return _api_success(**payload)


@zb_api_bp.get("/zb_api/session-evaluations/summary")
def api_session_evaluations_summary():
    """Return review counts plus the active cloud storage locations."""
    _require_admin_auth()
    try:
        rows = _load_review_state_for_api()
    except RuntimeError as exc:
        return _api_storage_unavailable(str(exc))
    except Exception:
        logging.exception("/zb_api/session-evaluations/summary failed")
        return _api_failure(
            "internal_server_error",
            "Unexpected server-side failure while loading review summary.",
            500,
        )

    return _api_success(
        summary=session_reviews.build_summary(rows),
        progress_summary=session_reviews.build_progress_summary(rows),
        storage=session_reviews.storage_metadata(),
    )


@zb_api_bp.get("/zb_api/session-evaluations/record/<int:index>")
def api_session_evaluations_record(index: int):
    """Fetch one review record from the cloud-backed review manifest."""
    _require_admin_auth()
    try:
        rows = _load_review_state_for_api()
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

    if index < 0 or index >= len(rows):
        return _api_failure(
            "review_record_not_found",
            "Record index out of range.",
            404,
            index=index,
        )
    return _api_success(record=session_reviews.serialize_record(rows, index))


@zb_api_bp.get("/zb_api/session-evaluations/needs-zb-review")
def api_session_evaluations_needs_zb_review():
    """List compact summaries for sessions still awaiting ZB review."""
    _require_admin_auth()
    try:
        rows = _load_review_state_for_api()
        offset = _parse_int_query_arg("offset", 0, 0, 1_000_000)
        limit = _parse_int_query_arg("limit", 25, 1, 100)
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

    matching_indices = session_reviews.indices_needing_reviewer(rows, "ZB")
    page_indices = matching_indices[offset : offset + limit]
    return _api_success(
        reviewer="ZB",
        total_matching=len(matching_indices),
        offset=offset,
        limit=limit,
        has_more=offset + limit < len(matching_indices),
        records=session_reviews.serialize_review_list_items(rows, page_indices),
    )


@zb_api_bp.get("/zb_api/session-evaluations/random-zb-review")
def api_session_evaluations_random_zb_review():
    """Return one random full record that still awaits ZB review."""
    _require_admin_auth()
    try:
        rows = _load_review_state_for_api()
        exclude_indices = _parse_exclude_indices_arg()
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

    candidate_indices = session_reviews.indices_needing_reviewer(rows, "ZB")
    available_count = len(
        [index for index in candidate_indices if index not in exclude_indices]
    )
    selected_indices = session_reviews.random_review_indices(
        rows,
        1,
        candidate_indices=candidate_indices,
        exclude_indices=exclude_indices,
    )
    selected_index = selected_indices[0] if selected_indices else None
    return _api_success(
        reviewer="ZB",
        available_count=available_count,
        selected_index=selected_index,
        record=(
            session_reviews.serialize_record(rows, selected_index)
            if selected_index is not None
            else None
        ),
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
        payload = {
            "queued_at": utipy._utc_now(),
            "conversation_id": args.conversation_id,
            "reviewer": args.reviewer,
            "note": args.note,
        }
        utipy._write_jsonl_record(
            utipy.config.REVIEW_REQUESTS_BLOB,
            payload,
            utipy._LOCAL_REVIEW_REQUESTS_PATH,
        )
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

    return _api_success(message="review request queued", **payload)


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

    expected_conversation_id = str(data.get("conversation_id", "")).strip()
    actual_conversation_id = str(rows[index].get("conversation_id", "")).strip()
    if expected_conversation_id and expected_conversation_id != actual_conversation_id:
        return _api_failure(
            "review_record_mismatch",
            (
                "Decision payload conversation_id does not match the current "
                "record at this index. Refetch the review record before saving."
            ),
            409,
            index=index,
            expected_conversation_id=expected_conversation_id,
            conversation_id=actual_conversation_id,
        )

    try:
        evaluation = session_reviews.normalize_evaluation(data.get("evaluation", ""))
    except ValueError as exc:
        return _api_failure("bad_request", str(exc), 400, index=index)
    if evaluation not in session_reviews.REVIEW_CHOICES:
        return _api_failure(
            "bad_request",
            "evaluation must be Use, Alter, or Reject.",
            400,
            index=index,
        )
    try:
        reviewer = session_reviews.normalize_reviewer(data.get("reviewer", "ZB"))
    except ValueError as exc:
        return _api_failure("bad_request", str(exc), 400, index=index)

    rows[index] = session_reviews.apply_reviewer_decision(
        rows[index], reviewer, evaluation
    )
    try:
        session_reviews.save_review_state(rows)
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
    return _api_success(
        record=session_reviews.serialize_record(rows, index),
        next_unreviewed_index=session_reviews.next_unreviewed_after(rows, index),
        summary=session_reviews.build_summary(rows),
        progress_summary=session_reviews.build_progress_summary(rows),
    )


@zb_api_bp.get("/zb_api/session-evaluations/random-sample")
def api_session_evaluations_random_sample():
    """Return compact summaries for a random unique sample of review rows."""
    _require_admin_auth()
    try:
        rows = _load_review_state_for_api()
        count = _parse_int_query_arg("count", 3, 1, 25)
        exclude_indices = _parse_exclude_indices_arg()
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

    available_count = len(
        [index for index in range(len(rows)) if index not in exclude_indices]
    )
    selected_indices = session_reviews.random_review_indices(
        rows,
        count,
        exclude_indices=exclude_indices,
    )
    return _api_success(
        requested_count=count,
        sampled_count=len(selected_indices),
        available_count=available_count,
        records=session_reviews.serialize_review_list_items(rows, selected_indices),
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

    next_index = session_reviews.find_next_matching_index(rows, after_index, desired)
    return _api_success(
        evaluation_filter=desired,
        next_index=next_index,
        record=(
            session_reviews.serialize_record(rows, next_index)
            if next_index is not None
            else None
        ),
    )


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

    logging.info("runtime_status_event stage=%s detail=%s", args.stage, args.detail)
    return _api_success(
        message="runtime status event reported",
        stage=args.stage,
        detail=args.detail,
    )
