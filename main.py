"""Primary Flask application for the Zenbot web shell and authenticated API.

This module is the orchestration layer for ``zb_app``. It wires together:

- browser-facing routes rendered with Jinja templates
- authenticated JSON API routes used by GPT Actions and external tools
- streaming and non-streaming chat flows backed by ``utilities.py``
- admin and review tooling for archives and training data curation
- hybrid deployment behavior split between App Engine and Cloud Run

The code here is intentionally thin where possible: storage, model access,
memory logbook handling, and koan helpers live in ``utilities.py``; runtime
configuration and secret resolution live in ``config.py``. Maintainers should
read this module as the request/response contract and high-level control flow
for the app.
"""

from __future__ import annotations

import csv
import hmac
import importlib.util
import io
import json
import logging
import os
import time
import zipfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    session,
    send_from_directory,
    send_file,
    stream_with_context,
    url_for,
)
from jinja2 import ChoiceLoader, FileSystemLoader
from werkzeug.exceptions import BadRequest
from werkzeug.exceptions import HTTPException

from review_sync import sync_review_queue
import utilities as utipy
from utilities import ModelAPIError

logging.basicConfig(level=utipy.config.LOG_LEVEL)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TRAINING_TEMPLATES_DIR = _REPO_ROOT / "training" / "templates"

app = Flask(__name__, static_url_path="/static")
app.jinja_loader = ChoiceLoader(
    [
        app.jinja_loader,
        FileSystemLoader(str(_TRAINING_TEMPLATES_DIR.resolve())),
    ]
)
app.secret_key = utipy.config.FLASK_SECRET_KEY

# Secure cookie defaults.
app.config["SESSION_COOKIE_SECURE"] = utipy.config.SESSION_COOKIE_SECURE
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SOURCE_JSONL"] = str(
    (_REPO_ROOT / "training" / "generated" / "collected_sessions_with_metadata.jsonl").resolve()
)
app.config["REVIEWED_JSONL"] = str(
    (_REPO_ROOT / "training" / "generated" / "collected_sessions_with_evaluations.jsonl").resolve()
)
app.config["TRAIN_JSONL"] = str(
    (_REPO_ROOT / "training" / "generated" / "sessions_to_train.jsonl").resolve()
)

_RATE_BUCKETS: dict[str, tuple[float, int]] = {}
_RATE_WINDOW_SECONDS = 60
_RATE_MAX_REQUESTS = 40
_ALLOWED_ORIGINS = {
    utipy.config.WEB_APP_ORIGIN.rstrip("/"),
    "http://localhost:8080",
    "http://127.0.0.1:8080",
}


@dataclass
class ChatTurnRequest:
    """Validated request payload for one user chat turn.

    Instances of this dataclass are created from browser or API JSON payloads
    before the request is handed to the conversation/session layer.
    """
    message: str
    conversation_id: str
    student: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChatTurnRequest":
        """Validate and normalize one inbound chat payload."""
        message = str(data.get("message", "")).strip()
        if not message:
            raise ValueError("No user input detected.")
        if len(message) > 2048:
            raise ValueError("Input exceeds max length (2048).")

        return cls(
            message=message,
            conversation_id=str(data.get("conversation_id", "")).strip(),
            student=str(data.get("student", "")).strip() or "webmonkE",
        )


def _utc_now() -> str:
    """Return a UTC timestamp string for API payloads and stored metadata."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _set_chat_cookies(
    response: Response, conversation_id: str, case_id: str = ""
) -> None:
    """Persist conversation cookies for browser-based chat flows."""
    response.set_cookie(
        "conversation_id",
        conversation_id,
        httponly=True,
        secure=utipy.config.SESSION_COOKIE_SECURE,
        samesite="Lax",
        path="/",
    )
    if case_id:
        response.set_cookie(
            "case_id",
            case_id,
            httponly=True,
            secure=utipy.config.SESSION_COOKIE_SECURE,
            samesite="Lax",
            path="/",
        )


def _clear_chat_cookies(response: Response) -> None:
    """Clear browser cookies that track the active chat session."""
    response.set_cookie("conversation_id", "", expires=0, path="/")
    response.set_cookie("case_id", "", expires=0, path="/")


def _maybe_rate_limit() -> Response | None:
    """Apply a simple in-memory rate limit to public chat ingress."""
    if request.path not in {"/chat"} and not request.path.startswith("/chat_case/"):
        return None

    ip = (
        request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")
        .split(",")[0]
        .strip()
    )
    now = time.time()

    window_start, count = _RATE_BUCKETS.get(ip, (now, 0))
    if now - window_start > _RATE_WINDOW_SECONDS:
        window_start, count = now, 0

    count += 1
    _RATE_BUCKETS[ip] = (window_start, count)

    if count > _RATE_MAX_REQUESTS:
        return make_response(
            jsonify({"error": "rate_limited", "message": "Too many requests."}), 429
        )
    return None


def _is_cors_chat_path(path: str) -> bool:
    """Return whether a route participates in cross-origin chat requests."""
    return path in {"/chat", "/save_chat"} or path.startswith("/chat_case/")


def _cors_preflight_response() -> Response:
    """Build a CORS preflight response for chat endpoints."""
    response = make_response("", 204)
    origin = request.headers.get("Origin", "")
    if origin and origin.rstrip("/") in _ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    return response


def _extract_auth_token() -> str:
    """Extract an API/admin token from the Authorization header.

    The server accepts either ``Bearer <token>`` or a raw token value to remain
    compatible with GPT Actions auth modes.
    """
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return auth.strip()


def _admin_session_active() -> bool:
    """Return whether the browser has an authenticated admin session."""
    return bool(session.get("admin_authenticated"))


def _safe_next_path(candidate: str) -> str:
    """Normalize a post-login redirect target to an internal path."""
    text = (candidate or "").strip()
    if not text.startswith("/") or text.startswith("//"):
        return url_for("admin_conversations")
    return text


def _require_api_auth() -> None:
    """Enforce bearer or raw-token auth for protected API routes."""
    expected = utipy.config.ACTION_API_TOKEN.strip()
    if not expected:
        abort(503, description="ACTION_API_TOKEN not configured")

    provided = _extract_auth_token()
    if not provided or not hmac.compare_digest(provided, expected):
        abort(401, description="Unauthorized")


def _require_admin_auth() -> None:
    """Enforce admin access via token header or browser session."""
    expected = utipy.config.ACTION_API_TOKEN.strip()
    if not expected:
        if utipy.config.LOCAL:
            return
        abort(503, description="Admin token not configured")

    provided = _extract_auth_token()
    if provided and hmac.compare_digest(provided, expected):
        return
    if _admin_session_active():
        return
    abort(403, description="Forbidden")


@app.before_request
def _request_guards():
    """Apply shared request guards before route handlers execute.

    This hook centralizes:
    - chat preflight/CORS handling
    - basic rate limiting for public chat routes
    - API auth for ``/zb_api/*`` and the legacy append route
    - admin auth and login redirects for browser-only admin pages
    """
    if request.method == "OPTIONS" and _is_cors_chat_path(request.path):
        return _cors_preflight_response()

    rate_limit_response = _maybe_rate_limit()
    if rate_limit_response is not None:
        return rate_limit_response

    path = request.path
    if path.startswith("/zb_api/") or path == "/appendMemoryLogbookEntry":
        if utipy.config.ZB_API_STRICT_AUTH:
            _require_api_auth()

    if path in {"/admin/login", "/admin/logout"}:
        return None

    if path.startswith("/admin") or path == "/download_chats":
        try:
            _require_admin_auth()
        except HTTPException:
            if request.method == "GET":
                next_path = request.full_path[:-1] if request.full_path.endswith("?") else request.full_path
                return redirect(url_for("admin_login", next=_safe_next_path(next_path)))
            raise


@app.after_request
def _apply_cors_headers(response: Response):
    """Attach CORS headers to chat responses when the origin is allowed."""
    if not _is_cors_chat_path(request.path):
        return response
    origin = request.headers.get("Origin", "")
    if origin and origin.rstrip("/") in _ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    return response


@app.context_processor
def inject_runtime_config() -> dict[str, Any]:
    """Expose chat runtime settings to Jinja templates."""
    return {
        "chat_api_base_url": (utipy.config.CHAT_API_BASE_URL or "").rstrip("/"),
        "streaming_enabled": utipy.config.STREAMING,
        "is_local": utipy.config.LOCAL,
    }


def _memory_mutation_response(
    status: str,
    memories: list[dict[str, Any]],
    entry: dict[str, Any] | None = None,
) -> Response:
    """Return a compact success payload for memory write operations."""
    payload: dict[str, Any] = {
        "status": status,
        "count": len(memories),
    }
    if entry:
        payload["serial_number"] = str(entry.get("serial_number", "")).strip()
        payload["title"] = str(entry.get("title", "")).strip()
    return make_response(jsonify(payload), 200)


def _parse_bounded_int_arg(
    name: str,
    default: int,
    minimum: int = 1,
    maximum: int = 25,
) -> int:
    """Parse and range-check a numeric query parameter."""
    raw_value = request.args.get(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"Query parameter '{name}' must be an integer.") from exc

    if value < minimum or value > maximum:
        raise ValueError(
            f"Query parameter '{name}' must be between {minimum} and {maximum}."
        )
    return value


# -------- Static/Web Routes --------
@app.route("/")
def home():
    """Render the public landing page."""
    return render_template("index.html")


@app.route("/privacy")
def privacy():
    """Render the privacy-policy page."""
    return render_template("privacy.html")


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    """Render and process the browser-based admin login form."""
    next_path = _safe_next_path(request.values.get("next", ""))
    error = ""

    if _admin_session_active():
        return redirect(next_path)

    if request.method == "POST":
        expected = utipy.config.ACTION_API_TOKEN.strip()
        token = request.form.get("token", "").strip()

        if not expected:
            abort(503, description="Admin token not configured")
        if token and hmac.compare_digest(token, expected):
            session["admin_authenticated"] = True
            return redirect(next_path)

        error = "Invalid admin token."

    return render_template("admin_login.html", error=error, next_path=next_path)


@app.route("/admin/logout", methods=["POST"])
def admin_logout():
    """End the browser admin session and redirect to the login page."""
    session.pop("admin_authenticated", None)
    return redirect(url_for("admin_login"))


@app.route("/chatter")
def chatter():
    """Render the main chat UI with the appropriate streaming template."""
    if utipy.config.STREAMING:
        return render_template("chatter_stream.html")
    return render_template("chatter.html")


@app.errorhandler(ModelAPIError)
def handle_model_error(err):
    """Translate model/provider exceptions into user-facing JSON errors."""
    logging.error("ModelAPIError: %s", err)
    return (
        jsonify(
            {
                "error": "model_error",
                "message": utipy.friendly_model_error_message(str(err)),
            }
        ),
        502,
    )


# -------- Chat Routes --------
@app.route("/chat", methods=["POST"])
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

        conversation_id = payload.conversation_id or request.cookies.get(
            "conversation_id", ""
        )
        if conversation_id:
            messages, metadata = utipy.get_conversation_state(conversation_id)
            if not metadata:
                metadata = utipy.reset_test(student=payload.student)
        else:
            conversation_id, messages, metadata = utipy.create_conversation(
                student=payload.student
            )

        params = utipy.get_or_init_params(metadata)

        if not utipy.config.STREAMING:
            messages = utipy.prompt_and_reply(messages, payload.message, params=params)
            metadata["updated_at"] = _utc_now()
            utipy.save_messages_to_firestore(
                conversation_id, messages, metadata=metadata
            )
            response = make_response(
                jsonify(
                    {
                        "response": messages[-1]["content"],
                        "conversation_id": conversation_id,
                        "status": "success",
                    }
                ),
                200,
            )
            _set_chat_cookies(response, conversation_id)
            return response

        def stream() -> Any:
            for event in utipy.prompt_and_stream(
                messages,
                payload.message,
                params=params,
                conversation_id=conversation_id,
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
        _set_chat_cookies(response, conversation_id)
        return response

    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except BadRequest as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logging.exception(
            "/chat unexpected error for conversation_id=%s", conversation_id
        )
        return jsonify({"error": str(e), "message": "Exception in /chat."}), 500


@app.route("/chat_case/<case_id>", methods=["POST"])
def chat_case(case_id: str):
    """Create a browser chat session anchored to a specific koan case."""
    try:
        if not case_id:
            return jsonify({"error": "No case_id provided."}), 400

        data = utipy.get_request_data()
        student = str(data.get("student", "")).strip() or "webmonkE"
        conversation_id, _messages, _metadata = utipy.create_conversation(
            student=student,
            case_id=case_id,
        )

        response = make_response(
            jsonify(
                {
                    "conversation_id": conversation_id,
                    "case_id": str(case_id),
                    "status": "success",
                }
            ),
            200,
        )
        _set_chat_cookies(response, conversation_id, case_id=str(case_id))
        return response
    except Exception as e:
        logging.exception("/chat_case error")
        return jsonify({"error": str(e)}), 500


@app.route("/save_chat", methods=["POST"])
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
            "saved_at": _utc_now(),
        }

        filename = f"{conversation_id}.jsonl"
        if utipy.config.LOCAL:
            filepath = os.path.join("config", "zbchats", filename)
            utipy.save_chat_to_file(messages, params, filepath)
            logging.info("Chat saved locally to %s", filepath)
        else:
            blob_name = f"zbchats/{filename}"
            utipy.save_chat_to_bucket(messages, params, blob_name)
            logging.info("Chat saved to GCS as %s", blob_name)

        utipy.delete_messages_from_firestore(conversation_id)
        response = make_response(jsonify({"status": "success"}), 200)
        _clear_chat_cookies(response)
        return response
    except Exception as e:
        logging.exception("/save_chat failed")
        return jsonify({"error": str(e)}), 500


# -------- External API Routes --------
@app.route("/zb_api/chat", methods=["POST"])
def zb_api_chat():
    """Process one authenticated API chat turn without browser cookies."""
    conversation_id = ""
    try:
        data = utipy.get_request_data()
        payload = ChatTurnRequest.from_dict(data)

        conversation_id = payload.conversation_id
        if conversation_id:
            messages, metadata = utipy.get_conversation_state(conversation_id)
            if not metadata:
                metadata = utipy.reset_test(student=payload.student)
        else:
            conversation_id, messages, metadata = utipy.create_conversation(
                student=payload.student
            )

        params = utipy.get_or_init_params(metadata)
        messages = utipy.prompt_and_reply(messages, payload.message, params=params)
        metadata["updated_at"] = _utc_now()
        utipy.save_messages_to_firestore(conversation_id, messages, metadata=metadata)

        return (
            jsonify(
                {
                    "status": "success",
                    "conversation_id": conversation_id,
                    "response": messages[-1]["content"],
                }
            ),
            200,
        )
    except ValueError as e:
        return (
            jsonify(
                {
                    "status": "failure",
                    "error": str(e),
                    "conversation_id": conversation_id or None,
                }
            ),
            400,
        )
    except Exception as e:
        logging.exception("/zb_api/chat failed")
        return (
            jsonify(
                {
                    "status": "failure",
                    "error": str(e),
                    "conversation_id": conversation_id or None,
                }
            ),
            500,
        )


@app.route("/zb_api/chat_case/<case_id>", methods=["POST"])
def zb_api_chat_case(case_id: str):
    """Start an authenticated API conversation anchored to one koan case."""
    conversation_id = ""
    try:
        data = utipy.get_request_data()
        student = str(data.get("student", "")).strip() or "api-case"

        conversation_id, _messages, _metadata = utipy.create_conversation(
            student=student,
            case_id=case_id,
        )
        return (
            jsonify(
                {
                    "status": "success",
                    "conversation_id": conversation_id,
                    "case_id": str(case_id),
                }
            ),
            200,
        )
    except Exception as e:
        logging.exception("/zb_api/chat_case failed")
        return (
            jsonify(
                {
                    "status": "failure",
                    "error": str(e),
                    "conversation_id": conversation_id or None,
                    "case_id": str(case_id),
                }
            ),
            500,
        )


@app.route("/zb_api/save_chat", methods=["POST"])
def zb_api_save_chat():
    """Archive an authenticated API conversation by conversation ID."""
    try:
        data = utipy.get_request_data()
        conversation_id = str(data.get("conversation_id", "")).strip()
        if not conversation_id:
            return (
                jsonify({"status": "failure", "error": "No conversation_id provided."}),
                400,
            )

        messages, metadata = utipy.get_conversation_state(conversation_id)
        if not messages:
            return (
                jsonify(
                    {
                        "status": "failure",
                        "error": "No messages found for conversation_id.",
                        "conversation_id": conversation_id,
                    }
                ),
                404,
            )

        params = {
            "conversation_id": conversation_id,
            "student": metadata.get("student", "api"),
            "case_id": metadata.get("case_id", ""),
            "model": metadata.get("model_name", ""),
            "profile": metadata.get("profile", ""),
            "loss": metadata.get("training_loss", 0.0),
            "saved_at": _utc_now(),
        }

        blob_name = f"zbchats/{conversation_id}.jsonl"
        utipy.save_chat_to_bucket(messages, params, blob_name)
        utipy.delete_messages_from_firestore(conversation_id)

        return jsonify({"status": "success", "error": "None"}), 200
    except Exception as e:
        logging.exception("/zb_api/save_chat failed")
        return jsonify({"status": "failure", "error": str(e)}), 500


@app.route("/zb_api/conversations/list", methods=["GET"])
def zb_api_conversations_list():
    """List archived conversation identifiers stored in Cloud Storage."""
    files = utipy.list_conversation_files_in_gcs()
    conversation_ids = [f.split("/")[-1].replace(".jsonl", "") for f in files]
    return jsonify({"status": "success", "conversation_ids": conversation_ids}), 200


@app.route("/zb_api/conversations/<conversation_id>", methods=["GET"])
def zb_api_conversation(conversation_id: str):
    """Fetch one archived conversation transcript from Cloud Storage."""
    if not conversation_id:
        return (
            jsonify(
                {
                    "conversation_id": None,
                    "messages": None,
                    "status": "Missing conversation_id.",
                }
            ),
            404,
        )
    messages = utipy.get_conversation_from_gcs(conversation_id)
    if not messages:
        return (
            jsonify(
                {
                    "conversation_id": conversation_id,
                    "messages": None,
                    "status": "Conversation not found.",
                }
            ),
            404,
        )
    return (
        jsonify(
            {
                "conversation_id": conversation_id,
                "messages": messages,
                "status": "success",
            }
        ),
        200,
    )


@app.route("/zb_api/load_memory_logbook", methods=["GET"])
def zb_api_load_memory_logbook():
    """Return a compact newest-first memory index for GPT-side selection."""
    try:
        limit = _parse_bounded_int_arg("limit", default=12, minimum=1, maximum=25)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    summaries, total_count = utipy.load_memory_logbook_summaries(limit=limit)
    return (
        jsonify(
            {
                "summaries": summaries,
                "status": "success" if total_count else "empty",
                "returned_count": len(summaries),
                "total_count": total_count,
                "limit": limit,
                "has_more": total_count > len(summaries),
            }
        ),
        200,
    )


@app.route("/zb_api/load_memory_entry/<serial_number>", methods=["GET"])
def zb_api_load_memory_entry(serial_number: str):
    """Fetch one canonical memory-logbook entry by serial number."""
    memory = utipy.get_memory_logbook_entry(serial_number)
    if not memory:
        return (
            jsonify(
                {
                    "status": "not_found",
                    "serial_number": str(serial_number),
                    "memory": None,
                }
            ),
            404,
        )

    return (
        jsonify(
            {
                "status": "success",
                "serial_number": str(serial_number),
                "memory": memory,
            }
        ),
        200,
    )


@app.route("/zb_api/load_memory_logbook_full", methods=["GET"])
def zb_api_load_memory_logbook_full():
    """Return the full canonical memory logbook for internal/admin use."""
    memories = utipy.load_memory_logbook()
    return (
        jsonify(
            {
                "memories": memories,
                "status": "success" if memories else "empty",
                "count": len(memories),
            }
        ),
        200,
    )


@app.route("/zb_api/update_memory_logbook", methods=["POST"])
def zb_api_update_memory_logbook():
    """Append one entry or replace the full memory logbook via the new API."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid or missing JSON body"}), 400

    if "entry" in data and "full_logbook" in data:
        return (
            jsonify(
                {
                    "error": (
                        "JSON body must contain either 'entry' or 'full_logbook', "
                        "not both"
                    )
                }
            ),
            400,
        )

    if "entry" in data:
        entry = data["entry"]
        if not isinstance(entry, dict):
            return jsonify({"error": "'entry' must be an object"}), 400
        try:
            updated = utipy.update_logbook(entry)
            saved_entry = updated[-1] if updated else utipy.normalize_memory_entry(entry)
            return _memory_mutation_response(
                "entry appended via POST", updated, entry=saved_entry
            )
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    if "full_logbook" in data:
        full = data["full_logbook"]
        if not isinstance(full, list):
            return jsonify({"error": "'full_logbook' must be an array"}), 400
        try:
            normalized = utipy.save_logbook(full)
            return _memory_mutation_response("logbook replaced via POST", normalized)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    if isinstance(data, dict):
        try:
            updated = utipy.update_logbook(data)
            saved_entry = updated[-1] if updated else utipy.normalize_memory_entry(data)
            return _memory_mutation_response(
                "entry appended via POST (legacy body)", updated, entry=saved_entry
            )
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    return (
        jsonify({"error": "JSON body must contain either 'entry' or 'full_logbook'"}),
        400,
    )


@app.route("/appendMemoryLogbookEntry", methods=["POST"])
def append_memory_logbook_entry_legacy():
    """Append one memory entry via the legacy compatibility endpoint."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid or missing JSON body"}), 400
    try:
        updated = utipy.update_logbook(data)
        saved_entry = updated[-1] if updated else utipy.normalize_memory_entry(data)
        return _memory_mutation_response(
            "entry appended (legacy endpoint)", updated, entry=saved_entry
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/zb_api/get_random_koan", methods=["GET"])
def zb_api_get_random_koan():
    """Return one random koan case as a flattened JSON payload."""
    case_id = utipy.get_random_koan_case_id()
    if not case_id:
        return (jsonify({"status": "Random number not generated."}), 404)

    koan = utipy.get_mmnk_case(case_id)
    if not koan:
        return (jsonify({"status": "koan not found"}), 404)

    payload = dict(koan)
    payload["status"] = "success"
    return (jsonify(payload), 200)


# -------- Source Text Pages --------
@app.route("/gg")
def gg():
    """Render the Gateless Gate table-of-contents page."""
    return render_template("gg.html")


@app.route("/gg/<id>")
def ggcase(id):
    """Render one Gateless Gate case page by case ID."""
    return render_template("ggcase.html", caseId=str(id))


@app.route("/bcr")
def display_pdf():
    """Render the Blue Cliff Record PDF viewer page."""
    return render_template("bcr.html")


@app.route("/pdf/<filename>")
def serve_pdf(filename):
    """Serve a static PDF file from the local static directory."""
    pdf_directory = os.path.join(os.getcwd(), "static")
    return send_from_directory(pdf_directory, filename)


# -------- Data Administration --------
def _repo_root() -> Path:
    """Return the Zenbot repository root above ``zb_app``."""
    return _REPO_ROOT


@lru_cache(maxsize=1)
def _session_review_module():
    """Load the standalone local session-review module for reuse in ``zb_app``."""
    module_path = _repo_root() / "training" / "session_review_app.py"
    spec = importlib.util.spec_from_file_location(
        "zenbot_session_review_app", module_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load session review module: {module_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _trainset04_review_csv_path() -> Path:
    """Return the CSV path that drives the admin review workflow."""
    return _repo_root() / "training" / "trainset04" / "review.csv"


def _collected_sessions_web_root() -> Path:
    """Return the local import directory for downloaded archived sessions."""
    return _repo_root() / "collected_sessions" / "web"


def _request_prefers_html() -> bool:
    """Return whether the caller looks like the browser admin UI."""
    return "text/html" in (request.headers.get("Accept", "").lower())


def _admin_conversations_response(status: str, **payload: Any):
    """Return a redirect for the admin UI or JSON for scripted callers."""
    body = {"status": status, **payload}
    if _request_prefers_html():
        return redirect(url_for("admin_conversations", **body))
    return jsonify(body), 200


def _archived_conversations_zip(
    archives: list[utipy.ArchivedConversation],
) -> io.BytesIO:
    """Package archived conversation JSONL blobs into a single zip file."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive_zip:
        for archive in archives:
            archive_zip.writestr(archive.filename, archive.content)
    buffer.seek(0)
    return buffer


def _load_review_rows() -> tuple[list[dict[str, str]], list[str]]:
    """Load training-review rows and headers from ``review.csv``."""
    review_path = _trainset04_review_csv_path()
    if not review_path.exists():
        abort(500, description=f"Missing review.csv at {review_path}")
    with review_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        headers = list(reader.fieldnames or [])
    return rows, headers


def _write_review_rows(headers: list[str], rows: list[dict[str, str]]) -> None:
    """Rewrite the review CSV after an admin decision update."""
    review_path = _trainset04_review_csv_path()
    with review_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def _update_review_keep(decisions: dict[str, str]) -> None:
    """Apply admin keep/discard decisions back to the review CSV."""
    rows, headers = _load_review_rows()
    if "id" not in headers or "keep" not in headers:
        abort(500, description="review.csv must include 'id' and 'keep' columns")

    changed = 0
    for r in rows:
        rid = (r.get("id") or "").strip()
        if rid in decisions:
            r["keep"] = decisions[rid]
            changed += 1

    _write_review_rows(headers, rows)
    logging.info("/admin/review wrote %s decisions", changed)


def _session_path_from_review_row(row: dict[str, str]) -> Path:
    """Resolve and validate a session path referenced from ``review.csv``."""
    rel = (row.get("source_path") or "").strip()
    if not rel:
        abort(500, description="review.csv row missing source_path")

    root = _repo_root().resolve()
    sessions_root = (root / "collected_sessions").resolve()
    candidate = (root / rel).resolve()

    if sessions_root != candidate and sessions_root not in candidate.parents:
        abort(400, description="Invalid source_path (not under collected_sessions)")
    if not candidate.exists():
        abort(404, description=f"Missing session file: {candidate}")
    return candidate


def _is_message_obj(obj: object) -> bool:
    """Return whether a JSON object looks like a stored chat message record."""
    if not isinstance(obj, dict):
        return False
    role = obj.get("role")
    content = obj.get("content")
    return role in {"system", "user", "assistant"} and isinstance(content, str)


def _parse_session_jsonl(path: Path) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Parse one reviewed session file into metadata and chat messages."""
    metadata: dict[str, Any] = {}
    messages: list[dict[str, str]] = []

    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue

        if _is_message_obj(obj):
            messages.append({"role": obj["role"], "content": obj["content"]})
        elif not metadata and isinstance(obj, dict):
            metadata = obj

    return metadata, messages


@app.route("/admin/review")
def admin_review():
    """Sync review inputs and open the dual-review session evaluation UI."""
    sync_review_queue(_repo_root())
    return redirect(url_for("review_page"))


@app.route("/review")
def review_page():
    """Render the local dual-review session evaluation browser UI."""
    _require_admin_auth()
    session_review = _session_review_module()
    try:
        rows = session_review.load_review_state(app)
    except ValueError as exc:
        abort(500, description=str(exc))

    requested_index = request.args.get("index", "").strip()
    if requested_index:
        try:
            index = int(requested_index)
        except ValueError:
            abort(400, description="Query parameter 'index' must be an integer")
    else:
        first_unreviewed = session_review.first_unreviewed_index(rows)
        index = first_unreviewed if first_unreviewed is not None else 0

    if index < 0 or index >= len(rows):
        abort(404, description="Record index out of range")

    record = session_review.serialize_record(rows, index)
    return render_template(
        "session_review.html",
        record=record,
        reviewed_path=app.config["REVIEWED_JSONL"],
        source_path=app.config["SOURCE_JSONL"],
        train_path=app.config["TRAIN_JSONL"],
    )


@app.post("/review/record/<int:index>/decision")
def set_decision(index: int):
    """Apply one browser-originated review decision and move to the next record."""
    _require_admin_auth()
    session_review = _session_review_module()
    try:
        rows = session_review.load_review_state(app)
    except ValueError as exc:
        abort(500, description=str(exc))

    if index < 0 or index >= len(rows):
        abort(404, description="Record index out of range")

    try:
        evaluation = session_review.normalize_evaluation(request.form.get("evaluation", ""))
    except ValueError as exc:
        abort(400, description=str(exc))
    if evaluation not in session_review.REVIEW_CHOICES:
        abort(400, description="evaluation must be Use, Alter, or Reject")
    try:
        reviewer = session_review.normalize_reviewer(
            request.form.get("reviewer", session_review.FORM_REVIEWER_DEFAULT)
        )
    except ValueError as exc:
        abort(400, description=str(exc))

    rows[index] = session_review.apply_reviewer_decision(rows[index], reviewer, evaluation)
    session_review.save_review_state(app, rows)

    redirect_index = session_review.next_unreviewed_after(rows, index)
    if redirect_index is None:
        redirect_index = session_review.next_index(rows, index)
    return redirect(url_for("review_page", index=redirect_index))


@app.get("/api/session-evaluations/summary")
@app.get("/zb_api/session-evaluations/summary")
def api_session_evaluations_summary():
    """Return review counts plus the active generated JSONL file paths."""
    _require_admin_auth()
    session_review = _session_review_module()
    try:
        rows = session_review.load_review_state(app)
    except ValueError as exc:
        abort(500, description=str(exc))

    return jsonify(
        {
            "status": "success",
            "summary": session_review.build_summary(rows),
            "paths": {
                "source_jsonl": app.config["SOURCE_JSONL"],
                "reviewed_jsonl": app.config["REVIEWED_JSONL"],
                "sessions_to_train_jsonl": app.config["TRAIN_JSONL"],
            },
        }
    )


@app.get("/api/session-evaluations/record/<int:index>")
@app.get("/zb_api/session-evaluations/record/<int:index>")
def api_session_evaluations_record(index: int):
    """Fetch one review record from the generated evaluation dataset."""
    _require_admin_auth()
    session_review = _session_review_module()
    try:
        rows = session_review.load_review_state(app)
    except ValueError as exc:
        abort(500, description=str(exc))

    if index < 0 or index >= len(rows):
        abort(404, description="Record index out of range")
    return jsonify(
        {"status": "success", "record": session_review.serialize_record(rows, index)}
    )


@app.post("/api/session-evaluations/record/<int:index>/decision")
@app.post("/zb_api/session-evaluations/record/<int:index>/decision")
def api_session_evaluations_decision(index: int):
    """Apply a JSON review decision for the requested reviewer and record."""
    _require_admin_auth()
    session_review = _session_review_module()
    try:
        rows = session_review.load_review_state(app)
    except ValueError as exc:
        abort(500, description=str(exc))

    if index < 0 or index >= len(rows):
        abort(404, description="Record index out of range")

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400, description="JSON body is required")

    try:
        evaluation = session_review.normalize_evaluation(data.get("evaluation", ""))
    except ValueError as exc:
        abort(400, description=str(exc))
    if evaluation not in session_review.REVIEW_CHOICES:
        abort(400, description="evaluation must be Use, Alter, or Reject")
    try:
        reviewer = session_review.normalize_reviewer(data.get("reviewer", "ZB"))
    except ValueError as exc:
        abort(400, description=str(exc))

    rows[index] = session_review.apply_reviewer_decision(rows[index], reviewer, evaluation)
    session_review.save_review_state(app, rows)
    return jsonify(
        {
            "status": "success",
            "record": session_review.serialize_record(rows, index),
            "next_unreviewed_index": session_review.next_unreviewed_after(rows, index),
        }
    )


@app.get("/api/session-evaluations/next")
@app.get("/zb_api/session-evaluations/next")
def api_session_evaluations_next():
    """Find the next record matching a requested review state."""
    _require_admin_auth()
    session_review = _session_review_module()
    try:
        rows = session_review.load_review_state(app)
    except ValueError as exc:
        abort(500, description=str(exc))

    desired = request.args.get("evaluation", "unreviewed").strip() or "unreviewed"
    if desired not in {"all", "unreviewed", "Use", "Alter", "Reject"}:
        abort(
            400,
            description="evaluation must be all, unreviewed, Use, Alter, or Reject",
        )
    after = request.args.get("after", "-1").strip()
    try:
        after_index = int(after)
    except ValueError:
        abort(400, description="Query parameter 'after' must be an integer")

    next_index = session_review.find_next_matching_index(rows, after_index, desired)
    return jsonify(
        {
            "status": "success",
            "evaluation_filter": desired,
            "next_index": next_index,
            "record": (
                session_review.serialize_record(rows, next_index)
                if next_index is not None
                else None
            ),
        }
    )


@app.route("/admin/review/legacy", methods=["GET", "POST"])
def admin_review_legacy():
    """Render the older CSV-backed keep/discard review queue."""
    rows, _headers = _load_review_rows()

    if request.method == "POST":
        decisions: dict[str, str] = {}
        for key in request.form.keys():
            if key.startswith("use_"):
                decisions[key[len("use_") :]] = "1"
            elif key.startswith("dont_"):
                decisions[key[len("dont_") :]] = "0"

        if decisions:
            _update_review_keep(decisions)

        return redirect(url_for("admin_review_legacy"))

    pending = [r for r in rows if not (r.get("keep") or "").strip()]

    try:
        offset = max(0, int(request.args.get("offset", 0)))
    except Exception:
        offset = 0
    try:
        limit = int(request.args.get("limit", 50))
    except Exception:
        limit = 50
    limit = min(max(10, limit), 200)

    page = pending[offset : offset + limit]

    return render_template(
        "admin_review.html",
        rows=page,
        pending_total=len(pending),
        offset=offset,
        limit=limit,
    )


@app.route("/admin/review/legacy/view/<record_id>")
def admin_review_view(record_id):
    """Render one reviewed training session from the legacy CSV queue."""
    rows, _headers = _load_review_rows()
    row = next((r for r in rows if (r.get("id") or "") == record_id), None)
    if not row:
        abort(404)

    session_path = _session_path_from_review_row(row)
    metadata, messages = _parse_session_jsonl(session_path)

    return render_template(
        "admin_review_view.html",
        row=row,
        session_path=str(session_path),
        metadata=metadata,
        messages=messages,
    )


@app.route("/admin/conversations")
def admin_conversations():
    """Render the admin list of archived conversation transcripts."""
    conversation_files = utipy.list_conversation_files_in_gcs()
    conversation_ids = [
        file_name.split("/")[-1].replace(".jsonl", "")
        for file_name in conversation_files
    ]
    return render_template(
        "admin_conversations.html",
        conversation_ids=conversation_ids,
        is_local=utipy.config.LOCAL,
    )


@app.route("/admin/conversations/<conversation_id>")
def admin_conversation_detail(conversation_id: str):
    """Render one archived conversation transcript in the admin UI."""
    if not conversation_id:
        abort(400)
    messages = utipy.get_conversation_from_gcs(conversation_id)
    if not messages:
        abort(404)
    return render_template(
        "admin_conversation_detail.html",
        conversation_id=conversation_id,
        messages=messages,
        is_local=utipy.config.LOCAL,
    )


@app.route("/admin/conversations/maintenance", methods=["POST"])
@app.route("/download_chats", methods=["POST"])
def download_chats():
    """Run bulk archive maintenance from the admin conversations page."""
    action = str(request.form.get("action", "download")).strip() or "download"
    allowed_actions = {"download", "download_delete", "delete", "sync_review"}
    if action not in allowed_actions:
        abort(400, description="Unsupported archive maintenance action")

    if action == "sync_review":
        if not utipy.config.LOCAL:
            return _admin_conversations_response("sync_unavailable")
        sync_summary = sync_review_queue(_repo_root())
        return _admin_conversations_response(
            "synced",
            synced=sync_summary["records_kept"],
            deduped=sync_summary["duplicates_moved"],
            preserved=sync_summary["decisions_preserved"],
            reviewed=sync_summary["evaluations_preserved"],
        )

    archives = utipy.download_archived_conversations()
    if not archives:
        return _admin_conversations_response("empty")

    if action == "delete":
        deleted = utipy.delete_archived_conversations(
            archive.blob_name for archive in archives
        )
        return _admin_conversations_response("deleted", deleted=deleted)

    if utipy.config.LOCAL:
        written_paths = utipy.write_archived_conversations_to_directory(
            archives, _collected_sessions_web_root()
        )
        deleted = 0
        if action == "download_delete":
            deleted = utipy.delete_archived_conversations(
                archive.blob_name for archive in archives
            )
        sync_summary = sync_review_queue(_repo_root())
        return _admin_conversations_response(
            "download_delete" if action == "download_delete" else "downloaded",
            downloaded=len(written_paths),
            deleted=deleted,
            synced=sync_summary["records_kept"],
            deduped=sync_summary["duplicates_moved"],
            preserved=sync_summary["decisions_preserved"],
            reviewed=sync_summary["evaluations_preserved"],
        )

    bundle = _archived_conversations_zip(archives)
    if action == "download_delete":
        utipy.delete_archived_conversations(archive.blob_name for archive in archives)

    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    return send_file(
        bundle,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"zbchats-{stamp}.zip",
    )


if __name__ == "__main__":
    if utipy.config.LOCAL:
        app.run(debug=True)
    else:
        app.run(host="0.0.0.0", port=8080, debug=False)
