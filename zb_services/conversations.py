"""Conversation lifecycle and archive workflows used by both transports."""

from typing import Any
import time

import session_reviews
import utilities as utipy
import storage_policy
from contracts import SessionSettingsInput
from .errors import ServiceError, require_archive_storage


def _utc_now() -> str:
    """Keep the existing API's second-resolution UTC archive timestamps."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def options() -> dict[str, Any]:
    """Return the runtime's canonical session options."""
    return utipy.session_options_payload()


def chat(
    message: str,
    conversation_id: str = "",
    student: str = "guest",
    settings: SessionSettingsInput | dict[str, Any] | None = None,
    case_id: str = "",
) -> dict[str, Any]:
    """Create, continue, or recreate a session and persist one exact turn."""
    conversation_id = conversation_id.strip()
    requested_id = conversation_id
    requested_unknown_id = ""
    effects_possible = False
    try:
        if requested_id:
            messages, metadata = utipy.get_conversation_state(requested_id)
        else:
            messages, metadata = [], {}
        if metadata:
            utipy.ensure_locked_session_settings(metadata, settings)
            conversation_status = "continued"
        else:
            effects_possible = True
            conversation_id, messages, metadata = utipy.create_conversation(
                student=student,
                settings=settings,
            )
            conversation_status = (
                "recreated_from_unknown_id" if requested_id else "created"
            )
            requested_unknown_id = requested_id
        params = utipy.get_or_init_params(metadata)
        effects_possible = True
        messages = utipy.prompt_and_reply(
            messages,
            message,
            params=params,
            metadata=metadata,
            conversation_id=conversation_id,
        )
        metadata["updated_at"] = _utc_now()
        utipy.save_messages_to_firestore(conversation_id, messages, metadata=metadata)
    except utipy.SessionSettingsLockedError as exc:
        raise ServiceError(
            "settings_locked",
            str(exc),
            409,
            session_settings=exc.current_settings,
            conversation_id=conversation_id or None,
        ) from exc
    except ValueError as exc:
        raise ServiceError(
            "bad_request",
            str(exc),
            conversation_id=conversation_id or None,
            effects_possible=effects_possible,
        ) from exc
    except Exception as exc:
        raise ServiceError(
            "internal_server_error",
            "Unexpected server-side failure while processing the turn.",
            500,
            conversation_id=conversation_id or None,
            effects_possible=effects_possible,
        ) from exc
    result: dict[str, Any] = {
        "conversation_id": conversation_id,
        "conversation_status": conversation_status,
        "response": messages[-1]["content"],
        "session_settings": utipy.session_settings_from_metadata(metadata),
    }
    if requested_unknown_id:
        result["requested_conversation_id"] = requested_unknown_id
    return result


def start_case(
    case_id: str,
    student: str = "api-case",
    settings: SessionSettingsInput | dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create an authenticated koan session with locked settings."""
    try:
        conversation_id, _, metadata = utipy.create_conversation(
            student=student,
            case_id=case_id,
            settings=settings,
        )
    except ValueError as exc:
        raise ServiceError("bad_request", str(exc), case_id=str(case_id)) from exc
    except Exception as exc:
        raise ServiceError(
            "internal_server_error",
            "Unexpected server-side failure while creating the koan conversation.",
            500,
            case_id=str(case_id),
            effects_possible=True,
        ) from exc
    return {
        "conversation_id": conversation_id,
        "case_id": str(case_id),
        "session_settings": metadata.get("session_settings", {}),
    }


def archive(conversation_id: str) -> dict[str, Any]:
    """Archive, update reviews, optionally submit a critic, then clear hot state."""
    require_archive_storage()
    if not conversation_id:
        raise ServiceError("bad_request", "No conversation_id provided.")
    messages, metadata = utipy.get_conversation_state(conversation_id)
    if not messages or (storage_policy.is_strict() and not metadata):
        raise ServiceError(
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
    session_reviews.upsert_review_record_from_session(conversation_id, messages, params)
    critic_id = utipy.submit_background_session_critic(
        messages, metadata, conversation_id
    )
    utipy.delete_messages_from_firestore(conversation_id)
    result: dict[str, Any] = {"conversation_id": conversation_id, "archived": True}
    if critic_id:
        result["background_critic_response_id"] = critic_id
    return result


def list_archives(
    offset: int = 0,
    limit: int = 25,
    case_id: str = "",
    student: str = "",
    start_date: str = "",
    end_date: str = "",
) -> dict[str, Any]:
    """Filter and paginate archive metadata without loading full transcripts."""
    require_archive_storage()
    records = utipy.list_conversation_records_in_gcs()
    filtered = []
    for record in records:
        saved_at = str(record.get("saved_at", ""))
        if case_id and str(record.get("case_id", "")) != case_id:
            continue
        if student and student.lower() not in str(record.get("student", "")).lower():
            continue
        if start_date and saved_at[:10] < start_date:
            continue
        if end_date and saved_at[:10] > end_date:
            continue
        filtered.append(record)
    page = filtered[offset : offset + limit]
    return {
        "conversation_ids": [str(row["conversation_id"]) for row in page],
        "records": page,
        "total_matching": len(filtered),
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < len(filtered),
    }


def get_archive(conversation_id: str) -> dict[str, Any]:
    """Load an archived transcript by stable conversation identifier."""
    require_archive_storage()
    messages = utipy.get_conversation_from_gcs(conversation_id)
    if not messages:
        raise ServiceError(
            "conversation_not_found",
            "Archived conversation not found.",
            404,
            conversation_id=conversation_id,
        )
    return {"conversation_id": conversation_id, "messages": messages}
