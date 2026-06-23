"""Primary Flask application for the Zenbot App Engine web shell and API.

This module is the orchestration layer for ``zb_app``. It wires together:

- browser-facing routes rendered with Jinja templates
- authenticated JSON API routes used by GPT Actions and external tools
- streaming and non-streaming chat flows backed by ``utilities.py``
- admin and review tooling for archives and training data curation
- single-surface App Engine deployment for browser, API, and admin flows

The code here is intentionally thin where possible: storage, model access,
memory logbook handling, and koan helpers live in ``utilities.py``; runtime
configuration and secret resolution live in ``config.py``. Maintainers should
read this module as the request/response contract and high-level control flow
for the app.
"""

from __future__ import annotations

import csv
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError
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
    stream_with_context,
    url_for,
)
from werkzeug.exceptions import BadRequest
from werkzeug.exceptions import HTTPException

from contracts import (
    EnqueueReviewArgs,
    ReportUiStatusArgs,
    SaveMemoryCandidateArgs,
    SearchExemplarsArgs,
    SessionStartRequest,
    SessionSettingsInput,
    TurnRequest,
)
import review_sync
import session_reviews
import utilities as utipy
from utilities import ModelAPIError, SessionSettingsLockedError

logging.basicConfig(level=utipy.config.LOG_LEVEL)
app = Flask(__name__, static_url_path="/static")
app.secret_key = utipy.config.FLASK_SECRET_KEY

# Secure cookie defaults.
app.config["SESSION_COOKIE_SECURE"] = utipy.config.SESSION_COOKIE_SECURE
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

_RATE_BUCKETS: dict[str, tuple[float, int]] = {}
_RATE_WINDOW_SECONDS = 60
_RATE_MAX_REQUESTS = 40


@dataclass
class ChatTurnRequest:
    """Validated request payload for one user chat turn.

    Instances of this dataclass are created from browser or API JSON payloads
    before the request is handed to the conversation/session layer.
    """
    message: str
    conversation_id: str
    case_id: str
    student: str
    settings: SessionSettingsInput | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChatTurnRequest":
        """Validate and normalize one inbound chat payload."""
        try:
            validated = TurnRequest.model_validate(
                {
                    "message": str(data.get("message", "")).strip(),
                    "conversation_id": str(data.get("conversation_id", "")).strip(),
                    "case_id": str(data.get("case_id", "")).strip(),
                    "student": str(data.get("student", "")).strip() or "guest",
                    "settings": data.get("settings"),
                }
            )
        except ValidationError as exc:
            message_errors = [
                err
                for err in exc.errors()
                if err.get("loc") and err["loc"][0] == "message"
            ]
            if message_errors:
                if any(error.get("type") == "string_too_long" for error in message_errors):
                    raise ValueError("Input exceeds max length (2048).") from exc
                raise ValueError("No user input detected.") from exc
            raise ValueError(str(exc)) from exc

        return cls(
            message=validated.message,
            conversation_id=validated.conversation_id,
            case_id=validated.case_id,
            student=validated.student or "guest",
            settings=validated.settings,
        )


@dataclass
class ChatStartRequest:
    """Validated request payload for starting a conversation before the first turn."""

    student: str
    settings: SessionSettingsInput | None

    @classmethod
    def from_dict(cls, data: dict[str, Any], default_student: str) -> "ChatStartRequest":
        """Validate and normalize one inbound chat-start payload."""

        try:
            validated = SessionStartRequest.model_validate(
                {
                    "student": str(data.get("student", "")).strip() or default_student,
                    "settings": data.get("settings"),
                }
            )
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc
        return cls(
            student=validated.student or default_student,
            settings=validated.settings,
        )


def _utc_now() -> str:
    """Return a UTC timestamp string for API payloads and stored metadata."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _normalize_origin(origin: str) -> str:
    """Return a normalized origin string suitable for CORS comparisons."""

    text = (origin or "").strip().rstrip("/")
    if not text:
        return ""
    parsed = urlsplit(text)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _configured_chat_origins() -> set[str]:
    """Return explicit chat origins allowed by runtime configuration."""

    origins = {
        _normalize_origin(utipy.config.WEB_APP_ORIGIN),
        "http://localhost:8080",
        "http://127.0.0.1:8080",
    }
    for origin in utipy.config.CHAT_ALLOWED_ORIGINS:
        origins.add(_normalize_origin(origin))
    return {origin for origin in origins if origin}


def _is_same_project_app_engine_origin(origin: str) -> bool:
    """Allow the default App Engine shell host for this project as a browser origin."""

    normalized = _normalize_origin(origin)
    if not normalized:
        return False

    hostname = (urlsplit(normalized).hostname or "").lower()
    project_id = str(utipy.config.GOOGLE_CLOUD_PROJECT or "").strip().lower()
    if not project_id:
        return False
    if hostname == f"{project_id}.appspot.com":
        return True
    return hostname.startswith(f"{project_id}.") and hostname.endswith(".r.appspot.com")


def _origin_is_allowed(origin: str) -> bool:
    """Return whether one browser Origin is permitted to call chat endpoints."""

    normalized = _normalize_origin(origin)
    if not normalized:
        return False
    return (
        normalized in _configured_chat_origins()
        or _is_same_project_app_engine_origin(normalized)
    )


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
    return path in {"/chat", "/chat/options", "/save_chat"} or path.startswith("/chat_case/")


def _is_api_json_path(path: str) -> bool:
    """Return whether a route should emit GPT-facing JSON error envelopes."""

    return path.startswith("/zb_api/")


def _cors_preflight_response() -> Response:
    """Build a CORS preflight response for chat endpoints."""
    response = make_response("", 204)
    origin = _normalize_origin(request.headers.get("Origin", ""))
    if origin and _origin_is_allowed(origin):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
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
    if path.startswith("/zb_api/"):
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
    origin = _normalize_origin(request.headers.get("Origin", ""))
    if origin and _origin_is_allowed(origin):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


@app.context_processor
def inject_runtime_config() -> dict[str, Any]:
    """Expose chat runtime settings to Jinja templates."""
    return {
        "public_session_options": utipy.session_options_payload(),
        "streaming_enabled": utipy.config.STREAMING,
        "is_local": utipy.config.LOCAL,
    }


def _default_api_error_code(status_code: int, description: str = "") -> str:
    """Map an HTTP status onto a stable API error code."""

    if status_code == 400:
        return "bad_request"
    if status_code == 401:
        return "unauthorized"
    if status_code == 403:
        return "forbidden"
    if status_code == 404:
        return "not_found"
    if status_code == 405:
        return "method_not_allowed"
    if status_code == 409:
        return "conflict"
    if status_code == 429:
        return "rate_limited"
    if status_code == 503:
        return (
            "storage_unavailable"
            if "storage" in str(description or "").lower()
            else "service_unavailable"
        )
    return "internal_server_error"


def _api_success(status_code: int = 200, **payload: Any) -> Response:
    """Return one normalized GPT-facing success envelope."""

    return make_response(jsonify({"status": "success", **payload}), status_code)


def _api_failure(
    error: str,
    message: str,
    status_code: int,
    **payload: Any,
) -> Response:
    """Return one normalized GPT-facing failure envelope."""

    body: dict[str, Any] = {
        "status": "failure",
        "error": str(error).strip() or "internal_server_error",
        "message": str(message).strip() or "Request failed.",
    }
    for key, value in payload.items():
        if value is not None:
            body[key] = value
    return make_response(jsonify(body), status_code)


def _api_storage_unavailable(
    message: str = "Required storage backend is unavailable.",
    **payload: Any,
) -> Response:
    """Return a normalized storage-unavailable API failure."""

    return _api_failure("storage_unavailable", message, 503, **payload)


def _api_archive_storage_guard(
    message: str = "Archive storage is unavailable or unconfigured.",
) -> Response | None:
    """Return a failure response when archive/review storage is unavailable."""

    if utipy.BUCKET:
        return None
    return _api_storage_unavailable(message)


def _load_review_state_for_api() -> list[dict[str, Any]]:
    """Load the review manifest for API routes or raise a storage/runtime error."""

    if not utipy.BUCKET:
        raise RuntimeError("Review storage is unavailable or unconfigured.")
    return session_reviews.load_review_state()


def _memory_mutation_response(
    message: str,
    memories: list[dict[str, Any]],
    entry: dict[str, Any] | None = None,
) -> Response:
    """Return a compact success payload for memory write operations."""
    payload: dict[str, Any] = {
        "message": str(message).strip(),
        "count": len(memories),
    }
    if entry:
        payload["serial_number"] = str(entry.get("serial_number", "")).strip()
        payload["title"] = str(entry.get("title", "")).strip()
    return _api_success(**payload)


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


def _parse_int_query_arg(
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    """Parse one integer query parameter with explicit inclusive bounds."""
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


def _parse_bool_query_arg(name: str, default: bool = False) -> bool:
    """Parse an optional boolean query parameter."""
    raw_value = request.args.get(name, "").strip().lower()
    if not raw_value:
        return default
    if raw_value in {"1", "true", "yes", "on"}:
        return True
    if raw_value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(
        f"Query parameter '{name}' must be true/false, yes/no, on/off, or 1/0."
    )


def _parse_exclude_indices_arg() -> set[int]:
    """Parse optional comma-separated non-negative review indices."""
    raw_value = request.args.get("exclude_indices", "").strip()
    if not raw_value:
        return set()

    excluded: set[int] = set()
    for item in raw_value.split(","):
        text = item.strip()
        if not text:
            raise ValueError(
                "Query parameter 'exclude_indices' must be a comma-separated "
                "list of non-negative integers."
            )
        try:
            index = int(text)
        except ValueError as exc:
            raise ValueError(
                "Query parameter 'exclude_indices' must be a comma-separated "
                "list of non-negative integers."
            ) from exc
        if index < 0:
            raise ValueError(
                "Query parameter 'exclude_indices' must be a comma-separated "
                "list of non-negative integers."
            )
        excluded.add(index)
    return excluded


# -------- Static/Web Routes --------
@app.route("/")
def home():
    """Render the public landing page."""
    return render_template("index.html")


@app.route("/intro-tour")
def intro_tour():
    """Render the public introductory zendo tour."""
    return render_template("intro_tour.html")


@app.route("/legacy-splash")
def legacy_splash():
    """Render the previous public splash page for reference."""
    return render_template("legacy_splash.html")


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


@app.route("/chat/options", methods=["GET"])
def chat_options():
    """Return the canonical browser session-settings options payload."""

    return jsonify(utipy.session_options_payload()), 200


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


@app.errorhandler(HTTPException)
def handle_http_exception(err: HTTPException):
    """Return JSON error envelopes for GPT-facing API routes."""

    if not _is_api_json_path(request.path):
        return err
    status_code = int(err.code or 500)
    return _api_failure(
        _default_api_error_code(status_code, str(err.description or "")),
        str(err.description or err.name or "Request failed."),
        status_code,
    )


def _settings_locked_response(
    err: SessionSettingsLockedError,
    api: bool = False,
    conversation_id: str = "",
):
    """Translate a settings-lock conflict into a user-facing JSON response."""

    payload: dict[str, Any] = {
        "error": "settings_locked",
        "message": str(err),
        "session_settings": err.current_settings,
    }
    if api:
        return _api_failure(
            "settings_locked",
            payload["message"],
            409,
            session_settings=err.current_settings,
            conversation_id=conversation_id or None,
        )
    return jsonify(payload), 409


def _resolve_api_chat_session(
    payload: ChatTurnRequest,
) -> tuple[str, list[dict[str, str]], dict[str, Any], str, str]:
    """Resolve API chat state for create/continue/recreate behavior."""

    requested_conversation_id = payload.conversation_id.strip()
    if not requested_conversation_id:
        conversation_id, messages, metadata = utipy.create_conversation(
            student=payload.student,
            settings=payload.settings,
        )
        return conversation_id, messages, metadata, "created", ""

    messages, metadata = utipy.get_conversation_state(requested_conversation_id)
    if metadata:
        utipy.ensure_locked_session_settings(metadata, payload.settings)
        return (
            requested_conversation_id,
            messages,
            metadata,
            "continued",
            "",
        )

    conversation_id, messages, metadata = utipy.create_conversation(
        student=payload.student,
        settings=payload.settings,
    )
    return (
        conversation_id,
        messages,
        metadata,
        "recreated_from_unknown_id",
        requested_conversation_id,
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
                        "session_settings": utipy.session_settings_from_metadata(metadata),
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


@app.route("/chat_case/<case_id>", methods=["POST"])
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
        logging.info("Chat saved to GCS as %s and upserted into review queue", blob_name)
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


# -------- External API Routes --------
@app.route("/zb_api/chat/options", methods=["GET"])
def zb_api_chat_options():
    """Return the canonical authenticated session-settings options payload."""

    return _api_success(**utipy.session_options_payload())


@app.route("/zb_api/chat", methods=["POST"])
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


@app.route("/zb_api/chat_case/<case_id>", methods=["POST"])
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


@app.route("/zb_api/save_chat", methods=["POST"])
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
        return _api_storage_unavailable(str(e), conversation_id=locals().get("conversation_id", None))
    except Exception:
        logging.exception("/zb_api/save_chat failed")
        return _api_failure(
            "internal_server_error",
            "Unexpected server-side failure while archiving the conversation.",
            500,
            conversation_id=locals().get("conversation_id", None),
        )


@app.route("/zb_api/conversations/list", methods=["GET"])
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


@app.route("/zb_api/conversations/<conversation_id>", methods=["GET"])
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


@app.route("/zb_api/load_memory_logbook", methods=["GET"])
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


@app.route("/zb_api/load_memory_entry/<serial_number>", methods=["GET"])
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


@app.route("/zb_api/exemplars/search", methods=["GET"])
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


@app.route("/zb_api/memory/candidates", methods=["POST"])
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


@app.route("/zb_api/update_memory_logbook", methods=["POST"])
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
            saved_entry = updated[-1] if updated else utipy.normalize_memory_entry(entry)
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


@app.route("/zb_api/get_random_koan", methods=["GET"])
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


@app.route("/zb_api/koans/by-title", methods=["GET"])
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


@app.route("/zb_api/koans/<case_id>", methods=["GET"])
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
    return Path(__file__).resolve().parents[1]


def _trainset04_review_csv_path() -> Path:
    """Return the CSV path that drives the admin review workflow."""
    return _repo_root() / "training" / "trainset04" / "review.csv"


def _request_prefers_html() -> bool:
    """Return whether the caller looks like the browser admin UI."""
    return "text/html" in (request.headers.get("Accept", "").lower())


def _admin_conversations_response(status: str, **payload: Any):
    """Return a redirect for the admin UI or JSON for scripted callers."""
    body = {"status": status, **payload}
    if _request_prefers_html():
        return redirect(url_for("admin_conversations", **body))
    return jsonify(body), 200


def _load_review_state_or_abort() -> list[dict[str, Any]]:
    """Load the cloud-backed review manifest or abort with a server error."""
    try:
        return session_reviews.load_review_state()
    except Exception as exc:
        abort(500, description=str(exc))


def _normalize_review_filter(value: str) -> str:
    """Validate the requested admin/API review filter."""
    desired = str(value or "needs_cm_review").strip() or "needs_cm_review"
    allowed = {
        "all",
        "needs_cm_review",
        "needs_zb_review",
        "unreviewed",
        "not_started",
        "awaiting_other_review",
        "cm_reviewed",
        "zb_reviewed",
        "Use",
        "Alter",
        "Reject",
    }
    if desired not in allowed:
        abort(
            400,
            description=(
                "evaluation must be all, needs_cm_review, needs_zb_review, "
                "unreviewed, not_started, awaiting_other_review, cm_reviewed, "
                "zb_reviewed, Use, Alter, or Reject"
            ),
        )
    return desired


def _admin_backend_status(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Return compact backend health rows for the admin review dashboard."""
    storage = session_reviews.storage_metadata()
    statuses: list[dict[str, str]] = []

    if utipy.BUCKET:
        statuses.append(
            {
                "label": "Archive Storage",
                "state": "Available",
                "class": "ok",
                "detail": (
                    f"Bucket {storage['bucket']} / prefix "
                    f"{storage['transcripts_prefix']}"
                ),
            }
        )
    else:
        statuses.append(
            {
                "label": "Archive Storage",
                "state": "Unavailable",
                "class": "warn",
                "detail": "No configured Cloud Storage bucket client.",
            }
        )

    statuses.append(
        {
            "label": "Review Manifest",
            "state": "Loaded",
            "class": "ok",
            "detail": (
                f"{len(rows)} row(s) in {storage['review_index_blob']}; "
                f"export {storage['sessions_to_train_blob']}"
            ),
        }
    )

    try:
        memories = utipy.load_memory_logbook()
    except Exception as exc:
        statuses.append(
            {
                "label": "Memory Logbook",
                "state": "Unavailable",
                "class": "warn",
                "detail": str(exc),
            }
        )
    else:
        statuses.append(
            {
                "label": "Memory Logbook",
                "state": "Loaded",
                "class": "ok",
                "detail": f"{len(memories)} normalized entries from {utipy.MEMORY_LOGBOOK}",
            }
        )

    vector_ids = list(utipy.config.OPENAI_VECTOR_STORE_IDS or [])
    if utipy.config.OPENAI_ENABLE_FILE_SEARCH and vector_ids:
        vector_state = "Enabled"
        vector_class = "ok"
        vector_detail = f"{len(vector_ids)} configured vector store id(s)."
    elif vector_ids:
        vector_state = "Configured"
        vector_class = "warn"
        vector_detail = "Vector store IDs exist, but File Search is disabled."
    else:
        vector_state = "Not configured"
        vector_class = "warn"
        vector_detail = "No vector store IDs configured for File Search."
    statuses.append(
        {
            "label": "Vector Store",
            "state": vector_state,
            "class": vector_class,
            "detail": vector_detail,
        }
    )

    hot_state_backend = utipy.config.HOT_STATE_BACKEND or "unset"
    statuses.append(
        {
            "label": "Hot-State Backend",
            "state": hot_state_backend,
            "class": "ok" if hot_state_backend != "unset" else "warn",
            "detail": "Configured runtime conversation-state backend.",
        }
    )
    return statuses


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
    """Redirect the older admin review entrypoint into the cloud dashboard."""
    return redirect(url_for("admin_conversations", evaluation="needs_cm_review"))


@app.route("/admin/session_settings")
def admin_session_settings():
    """Render the admin-only placeholder for future session-settings controls."""

    options = utipy.session_options_payload()
    planned_endpoints = [
        {
            "path": "/admin/session_settings",
            "label": "Session Settings Overview",
            "description": "Read-only overview of current public defaults and future control areas.",
        },
        {
            "path": "/admin/session_settings/presets",
            "label": "Preset Management",
            "description": "Future editor for Mumonbot-ling presets, instruction bundles, and prompt variants.",
        },
        {
            "path": "/admin/session_settings/public_runtime",
            "label": "Public Dokusan Runtime",
            "description": "Future controls for public-session defaults, rotation, and random assignment policy.",
        },
        {
            "path": "/admin/session_settings/tools",
            "label": "Tooling And Critic Controls",
            "description": "Future controls for function tools, search tools, and background review behavior.",
        },
    ]
    return render_template(
        "admin_session_settings.html",
        options=options,
        planned_endpoints=planned_endpoints,
    )


@app.route("/review")
def review_page():
    """Render one cloud-backed review record in the browser admin UI."""
    _require_admin_auth()
    rows = _load_review_state_or_abort()
    if not rows:
        return redirect(url_for("admin_conversations", status="empty"))

    requested_index = request.args.get("index", "").strip()
    if requested_index:
        try:
            index = int(requested_index)
        except ValueError:
            abort(400, description="Query parameter 'index' must be an integer")
    else:
        first_cm_review = session_reviews.first_index_needing_reviewer(
            rows, session_reviews.FORM_REVIEWER_DEFAULT
        )
        if first_cm_review is not None:
            index = first_cm_review
        else:
            first_unreviewed = session_reviews.first_unreviewed_index(rows)
            index = first_unreviewed if first_unreviewed is not None else 0

    if index < 0 or index >= len(rows):
        abort(404, description="Record index out of range")

    dashboard_filter = _normalize_review_filter(
        request.args.get("evaluation", "needs_cm_review")
    )
    record = session_reviews.serialize_record(rows, index)
    return render_template(
        "session_review.html",
        record=record,
        dashboard_filter=dashboard_filter,
        progress_summary=session_reviews.build_progress_summary(rows),
        storage=session_reviews.storage_metadata(),
    )


@app.post("/review/record/<int:index>/decision")
def set_decision(index: int):
    """Apply one browser-originated review decision and move to the next record."""
    _require_admin_auth()
    rows = _load_review_state_or_abort()

    if index < 0 or index >= len(rows):
        abort(404, description="Record index out of range")

    try:
        evaluation = session_reviews.normalize_evaluation(
            request.form.get("evaluation", "")
        )
    except ValueError as exc:
        abort(400, description=str(exc))
    if evaluation not in session_reviews.REVIEW_CHOICES:
        abort(400, description="evaluation must be Use, Alter, or Reject")
    try:
        reviewer = session_reviews.normalize_reviewer(
            request.form.get("reviewer", session_reviews.FORM_REVIEWER_DEFAULT)
        )
    except ValueError as exc:
        abort(400, description=str(exc))

    rows[index] = session_reviews.apply_reviewer_decision(
        rows[index], reviewer, evaluation
    )
    saved_row = dict(rows[index])
    session_reviews.save_review_state(rows)

    dashboard_filter = _normalize_review_filter(
        request.form.get("dashboard_filter", "needs_cm_review")
    )
    redirect_index = session_reviews.next_index_needing_reviewer_after(
        rows, index, reviewer
    )
    saved_redirect_args = {
        "saved_conversation_id": saved_row.get("conversation_id", ""),
        "saved_index": index,
        "saved_reviewer": reviewer,
        "saved_evaluation": evaluation,
        "saved_status": session_reviews.status_label(saved_row),
    }
    if redirect_index is None and dashboard_filter == "needs_cm_review":
        return redirect(
            url_for(
                "admin_conversations",
                evaluation=dashboard_filter,
                **saved_redirect_args,
            )
        )
    if redirect_index is None:
        redirect_index = session_reviews.next_unreviewed_after(rows, index)
    if redirect_index is None:
        redirect_index = session_reviews.next_index(rows, index)
    return redirect(
        url_for(
            "review_page",
            index=redirect_index,
            evaluation=dashboard_filter,
            **saved_redirect_args,
        )
    )


@app.get("/zb_api/session-evaluations/summary")
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


@app.get("/zb_api/session-evaluations/record/<int:index>")
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
    return _api_success(
        record=session_reviews.serialize_record(rows, index)
    )


@app.get("/zb_api/session-evaluations/needs-zb-review")
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


@app.get("/zb_api/session-evaluations/random-zb-review")
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


@app.post("/zb_api/session-evaluations/review-requests")
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


@app.post("/zb_api/session-evaluations/record/<int:index>/decision")
def api_session_evaluations_decision(index: int):
    """Apply a JSON review decision for the requested reviewer and record."""
    _require_admin_auth()
    try:
        rows = _load_review_state_for_api()
    except RuntimeError as exc:
        return _api_storage_unavailable(str(exc), index=index)
    except Exception:
        logging.exception("/zb_api/session-evaluations/record/%s/decision failed", index)
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
        logging.exception("/zb_api/session-evaluations/record/%s/decision save failed", index)
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


@app.get("/zb_api/session-evaluations/random-sample")
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


@app.get("/zb_api/session-evaluations/next")
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


@app.post("/zb_api/runtime/status-events")
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
    """Render the cloud-backed review dashboard."""
    rows = _load_review_state_or_abort()
    evaluation = _normalize_review_filter(
        request.args.get("evaluation", "needs_cm_review")
    )
    review_rows = session_reviews.list_dashboard_rows(rows, evaluation)
    return render_template(
        "admin_conversations.html",
        summary=session_reviews.build_summary(rows),
        progress_summary=session_reviews.build_progress_summary(rows),
        evaluation=evaluation,
        review_rows=review_rows,
        next_cm_review_index=session_reviews.first_index_needing_reviewer(
            rows, session_reviews.FORM_REVIEWER_DEFAULT
        ),
        storage=session_reviews.storage_metadata(),
        backend_status=_admin_backend_status(rows),
    )


@app.get("/admin/memory_logbook/download")
def admin_memory_logbook_download():
    """Download the full normalized memory logbook from a local admin server."""
    _require_admin_auth()
    if not utipy.config.LOCAL:
        abort(403, description="Memory logbook download is only available locally")
    try:
        memories = utipy.load_memory_logbook()
    except RuntimeError as exc:
        abort(503, description=str(exc))
    payload = json.dumps(memories, ensure_ascii=False, indent=2) + "\n"
    response = Response(payload, mimetype="application/json")
    response.headers["Content-Disposition"] = (
        'attachment; filename="memory_logbook.json"'
    )
    return response


@app.route("/admin/conversations/<conversation_id>")
def admin_conversation_detail(conversation_id: str):
    """Render one archived transcript plus a link back into the review flow."""
    if not conversation_id:
        abort(400)
    rows = _load_review_state_or_abort()
    messages = utipy.get_conversation_from_gcs(conversation_id)
    if not messages:
        abort(404)
    review_index = next(
        (
            index
            for index, row in enumerate(rows)
            if row.get("conversation_id") == conversation_id
        ),
        None,
    )
    return render_template(
        "admin_conversation_detail.html",
        conversation_id=conversation_id,
        messages=messages,
        review_index=review_index,
    )


@app.route("/admin/conversations/maintenance", methods=["POST"])
@app.route("/download_chats", methods=["POST"])
def download_chats():
    """Run non-destructive review maintenance from the admin dashboard."""
    action = str(request.form.get("action", "backfill_review")).strip() or "backfill_review"
    allowed_actions = {"backfill_review", "sync_local_remote"}
    if action not in allowed_actions:
        abort(400, description="Unsupported archive maintenance action")

    if action == "sync_local_remote":
        if not utipy.config.LOCAL:
            abort(403, description="Local/remote sync is only available locally")
        downloaded = utipy.download_all()
        backfill_summary = session_reviews.backfill_review_state(_repo_root())
        sync_summary = review_sync.sync_review_queue(_repo_root())
        return _admin_conversations_response(
            "synced",
            downloaded="1" if downloaded else "0",
            scanned=backfill_summary["transcripts_scanned"],
            rows=backfill_summary["review_rows"],
            preserved_existing=backfill_summary["preserved_existing_reviews"],
            preserved_legacy=backfill_summary["preserved_legacy_reviews"],
            train_rows=backfill_summary["sessions_to_train_rows"],
            records_scanned=sync_summary["records_scanned"],
            records_kept=sync_summary["records_kept"],
            duplicates_moved=sync_summary["duplicates_moved"],
            decisions_preserved=sync_summary["decisions_preserved"],
            generated_rows=sync_summary["generated_rows"],
        )

    summary = session_reviews.backfill_review_state(_repo_root())
    return _admin_conversations_response(
        "backfilled",
        scanned=summary["transcripts_scanned"],
        rows=summary["review_rows"],
        preserved_existing=summary["preserved_existing_reviews"],
        preserved_legacy=summary["preserved_legacy_reviews"],
        train_rows=summary["sessions_to_train_rows"],
    )


if __name__ == "__main__":
    if utipy.config.LOCAL:
        app.run(debug=True)
    else:
        app.run(host="0.0.0.0", port=8080, debug=False)
