"""Shared Flask HTTP contracts, request guards, and response helpers.

Route blueprints import this module for validated request payloads, browser and
API authentication, CORS/rate-limit enforcement, and stable JSON envelopes.
It deliberately owns cross-cutting web behavior rather than any endpoint.
"""

from __future__ import annotations

import hmac
import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    make_response,
    redirect,
    request,
    session,
    url_for,
)
from pydantic import ValidationError
from werkzeug.exceptions import HTTPException

from contracts import SessionStartRequest, SessionSettingsInput, TurnRequest
import session_reviews
import utilities as utipy
from utilities import ModelAPIError, SessionSettingsLockedError

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
                if any(
                    error.get("type") == "string_too_long" for error in message_errors
                ):
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
    def from_dict(
        cls, data: dict[str, Any], default_student: str
    ) -> "ChatStartRequest":
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
    return path in {"/chat", "/chat/options", "/save_chat"} or path.startswith(
        "/chat_case/"
    )


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
        return url_for("admin.admin_conversations")
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
                next_path = (
                    request.full_path[:-1]
                    if request.full_path.endswith("?")
                    else request.full_path
                )
                return redirect(
                    url_for("admin.admin_login", next=_safe_next_path(next_path))
                )
            raise


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


def _settings_locked_response(
    err: SessionSettingsLockedError,
    api: bool = False,
    conversation_id: str = "",
) -> Response | tuple[Response, int]:
    """Translate a locked-session settings conflict into a JSON response."""
    if api:
        return _api_failure(
            "settings_locked",
            str(err),
            409,
            session_settings=err.current_settings,
            conversation_id=conversation_id or None,
        )
    return (
        jsonify(
            {
                "error": "settings_locked",
                "message": str(err),
                "session_settings": err.current_settings,
            }
        ),
        409,
    )


def _resolve_api_chat_session(
    payload: ChatTurnRequest,
) -> tuple[str, list[dict[str, str]], dict[str, Any], str, str]:
    """Resolve create, continue, or recreate behavior for an API chat turn."""
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
        return requested_conversation_id, messages, metadata, "continued", ""

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


def _normalize_review_filter(value: str) -> str:
    """Validate one supported session-review dashboard or API filter."""
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


def register_app_handlers(app: Flask) -> None:
    """Attach application-wide request, response, template, and error hooks."""
    app.before_request(_request_guards)
    app.after_request(_apply_cors_headers)
    app.context_processor(inject_runtime_config)
    app.register_error_handler(ModelAPIError, handle_model_error)
    app.register_error_handler(HTTPException, handle_http_exception)
