"""Public pages and browser-session chat routes for the Zenbot web shell."""

from __future__ import annotations

import logging
import os
from typing import Any

from flask import (
    Blueprint,
    Response,
    jsonify,
    make_response,
    render_template,
    request,
    send_from_directory,
    stream_with_context,
)
from werkzeug.exceptions import BadRequest

import session_reviews
import utilities as utipy
from app_support import (
    ChatStartRequest,
    ChatTurnRequest,
    _clear_chat_cookies,
    _settings_locked_response,
    _set_chat_cookies,
    _utc_now,
)
from utilities import SessionSettingsLockedError

web_bp = Blueprint("web", __name__)


@web_bp.route("/")
def home():
    """Render the public landing page."""
    return render_template("index.html")


@web_bp.route("/intro-tour")
def intro_tour():
    """Render the public introductory zendo tour."""
    return render_template("intro_tour.html")


@web_bp.route("/legacy-splash")
def legacy_splash():
    """Render the previous public splash page for reference."""
    return render_template("legacy_splash.html")


@web_bp.route("/privacy")
def privacy():
    """Render the privacy-policy page."""
    return render_template("privacy.html")


@web_bp.route("/chatter")
def chatter():
    """Render the main chat UI with the appropriate streaming template."""
    if utipy.config.STREAMING:
        return render_template("chatter_stream.html")
    return render_template("chatter.html")


@web_bp.route("/chat/options", methods=["GET"])
def chat_options():
    """Return the canonical browser session-settings options payload."""

    return jsonify(utipy.session_options_payload()), 200


@web_bp.route("/chat", methods=["POST"])
def chat():
    """Handle one browser chat turn and optionally stream the response.

    Inputs come from the browser's JSON body plus cookie-backed conversation
    state. Outputs are either JSON or Server-Sent Events, and the handler
    persists conversation state via ``utilities.py`` after each assistant turn.
    """
    conversation_id = ""
    try:
        data = utipy.get_request_data()
        payload = ChatTurnRequest.from_dict(data)

        conversation_id = (
            payload.conversation_id
            if "conversation_id" in data
            else request.cookies.get("conversation_id", "")
        )
        if conversation_id:
            messages, metadata = utipy.get_conversation_state(conversation_id)
            if not metadata:
                metadata = utipy.reset_test(
                    student=payload.student,
                    settings=payload.settings,
                )
            utipy.ensure_locked_session_settings(metadata, payload.settings)
        else:
            conversation_id, messages, metadata = utipy.create_conversation(
                student=payload.student,
                case_id=payload.case_id or None,
                settings=payload.settings,
            )

        params = utipy.get_or_init_params(metadata)

        if not utipy.config.STREAMING:
            messages = utipy.prompt_and_reply(
                messages,
                payload.message,
                params=params,
                metadata=metadata,
                conversation_id=conversation_id,
            )
            metadata["updated_at"] = _utc_now()
            utipy.save_messages_to_firestore(
                conversation_id, messages, metadata=metadata
            )
            response = make_response(
                jsonify(
                    {
                        "response": messages[-1]["content"],
                        "conversation_id": conversation_id,
                        "session_settings": utipy.session_settings_from_metadata(
                            metadata
                        ),
                        "status": "success",
                    }
                ),
                200,
            )
            _set_chat_cookies(response, conversation_id, case_id=payload.case_id)
            return response

        def stream() -> Any:
            for event in utipy.prompt_and_stream(
                messages,
                payload.message,
                params=params,
                conversation_id=conversation_id,
                metadata=metadata,
                sync_reply_only=utipy.session_requires_sync(metadata),
            ):
                yield event

        response = Response(stream_with_context(stream()), mimetype="text/event-stream")
        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Accel-Buffering"] = "no"

        def persist_after_stream() -> None:
            metadata["updated_at"] = _utc_now()
            utipy.save_messages_to_firestore(
                conversation_id, messages, metadata=metadata
            )

        response.call_on_close(persist_after_stream)
        _set_chat_cookies(response, conversation_id, case_id=payload.case_id)
        return response

    except SessionSettingsLockedError as e:
        return _settings_locked_response(e)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except BadRequest as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logging.exception(
            "/chat unexpected error for conversation_id=%s", conversation_id
        )
        return jsonify({"error": str(e), "message": "Exception in /chat."}), 500


@web_bp.route("/chat_case/<case_id>", methods=["POST"])
def chat_case(case_id: str):
    """Create a browser chat session anchored to a specific koan case."""
    try:
        if not case_id:
            return jsonify({"error": "No case_id provided."}), 400

        data = utipy.get_request_data()
        payload = ChatStartRequest.from_dict(data, default_student="guest")
        conversation_id, _messages, _metadata = utipy.create_conversation(
            student=payload.student,
            case_id=case_id,
            settings=payload.settings,
        )

        response = make_response(
            jsonify(
                {
                    "conversation_id": conversation_id,
                    "case_id": str(case_id),
                    "session_settings": _metadata.get("session_settings", {}),
                    "status": "success",
                }
            ),
            200,
        )
        _set_chat_cookies(response, conversation_id, case_id=str(case_id))
        return response
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logging.exception("/chat_case error")
        return jsonify({"error": str(e)}), 500


@web_bp.route("/save_chat", methods=["POST"])
def save_chat():
    """Archive the active browser chat and clear its live working state."""
    try:
        data = utipy.get_request_data()
        conversation_id = str(
            data.get("conversation_id", "")
        ).strip() or request.cookies.get("conversation_id", "")
        if not conversation_id:
            return jsonify({"error": "No conversation to save."}), 400

        messages, metadata = utipy.get_conversation_state(conversation_id)
        if not messages:
            return jsonify({"error": "No conversation to save."}), 404

        params = {
            "conversation_id": conversation_id,
            "student": metadata.get("student", "unk"),
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
        logging.info(
            "Chat saved to GCS as %s and upserted into review queue", blob_name
        )
        if critic_response_id:
            logging.info(
                "Background critic queued for %s as %s",
                conversation_id,
                critic_response_id,
            )

        utipy.delete_messages_from_firestore(conversation_id)
        response = make_response(jsonify({"status": "success"}), 200)
        _clear_chat_cookies(response)
        return response
    except Exception as e:
        logging.exception("/save_chat failed")
        return jsonify({"error": str(e)}), 500


@web_bp.route("/gg")
def gg():
    """Render the Gateless Gate table-of-contents page."""
    return render_template("gg.html")


@web_bp.route("/gg/<id>")
def ggcase(id):
    """Render one Gateless Gate case page by case ID."""
    return render_template("ggcase.html", caseId=str(id))


@web_bp.route("/bcr")
def display_pdf():
    """Render the Blue Cliff Record PDF viewer page."""
    return render_template("bcr.html")


@web_bp.route("/pdf/<filename>")
def serve_pdf(filename):
    """Serve a static PDF file from the local static directory."""
    pdf_directory = os.path.join(os.getcwd(), "static")
    return send_from_directory(pdf_directory, filename)
