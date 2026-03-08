# Web App: Zenbot Dokusan -- https://zenbot-434517.uw.r.appspot.com/
# API schema lives in ../zenbot_knowledge/action_schemas.yaml

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

import utilities as utipy
from utilities import ModelAPIError

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
_ALLOWED_ORIGINS = {
    utipy.config.WEB_APP_ORIGIN.rstrip("/"),
    "http://localhost:8080",
    "http://127.0.0.1:8080",
}


@dataclass
class ChatTurnRequest:
    message: str
    conversation_id: str
    student: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChatTurnRequest":
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
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _set_chat_cookies(
    response: Response, conversation_id: str, case_id: str = ""
) -> None:
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
    response.set_cookie("conversation_id", "", expires=0, path="/")
    response.set_cookie("case_id", "", expires=0, path="/")


def _maybe_rate_limit() -> Response | None:
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
    return path in {"/chat", "/save_chat"} or path.startswith("/chat_case/")


def _cors_preflight_response() -> Response:
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
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return auth.strip()


def _admin_session_active() -> bool:
    return bool(session.get("admin_authenticated"))


def _safe_next_path(candidate: str) -> str:
    text = (candidate or "").strip()
    if not text.startswith("/") or text.startswith("//"):
        return url_for("admin_conversations")
    return text


def _require_api_auth() -> None:
    expected = utipy.config.ACTION_API_TOKEN.strip()
    if not expected:
        abort(503, description="ACTION_API_TOKEN not configured")

    provided = _extract_auth_token()
    if not provided or not hmac.compare_digest(provided, expected):
        abort(401, description="Unauthorized")


def _require_admin_auth() -> None:
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
    return {
        "chat_api_base_url": (utipy.config.CHAT_API_BASE_URL or "").rstrip("/"),
        "streaming_enabled": utipy.config.STREAMING,
    }


def _memory_mutation_response(
    status: str,
    memories: list[dict[str, Any]],
    entry: dict[str, Any] | None = None,
) -> Response:
    payload: dict[str, Any] = {
        "status": status,
        "count": len(memories),
    }
    if entry:
        payload["serial_number"] = str(entry.get("serial_number", "")).strip()
        payload["title"] = str(entry.get("title", "")).strip()
    return make_response(jsonify(payload), 200)


# -------- Static/Web Routes --------
@app.route("/")
def home():
    return render_template("index.html")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
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
    session.pop("admin_authenticated", None)
    return redirect(url_for("admin_login"))


@app.route("/chatter")
def chatter():
    if utipy.config.STREAMING:
        return render_template("chatter_stream.html")
    return render_template("chatter.html")


@app.errorhandler(ModelAPIError)
def handle_model_error(err):
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
    files = utipy.list_conversation_files_in_gcs()
    conversation_ids = [f.split("/")[-1].replace(".jsonl", "") for f in files]
    return jsonify({"status": "success", "conversation_ids": conversation_ids}), 200


@app.route("/zb_api/conversations/<conversation_id>", methods=["GET"])
def zb_api_conversation(conversation_id: str):
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
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid or missing JSON body"}), 400

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
            utipy.save_logbook(full)
            normalized = utipy.normalize_logbook_entries(full)
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
    return render_template("gg.html")


@app.route("/gg/<id>")
def ggcase(id):
    return render_template("ggcase.html", caseId=str(id))


@app.route("/bcr")
def display_pdf():
    return render_template("bcr.html")


@app.route("/pdf/<filename>")
def serve_pdf(filename):
    pdf_directory = os.path.join(os.getcwd(), "static")
    return send_from_directory(pdf_directory, filename)


# -------- Data Administration --------
def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _trainset04_review_csv_path() -> Path:
    return _repo_root() / "training" / "trainset04" / "review.csv"


def _load_review_rows() -> tuple[list[dict[str, str]], list[str]]:
    review_path = _trainset04_review_csv_path()
    if not review_path.exists():
        abort(500, description=f"Missing review.csv at {review_path}")
    with review_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        headers = list(reader.fieldnames or [])
    return rows, headers


def _write_review_rows(headers: list[str], rows: list[dict[str, str]]) -> None:
    review_path = _trainset04_review_csv_path()
    with review_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def _update_review_keep(decisions: dict[str, str]) -> None:
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
    if not isinstance(obj, dict):
        return False
    role = obj.get("role")
    content = obj.get("content")
    return role in {"system", "user", "assistant"} and isinstance(content, str)


def _parse_session_jsonl(path: Path) -> tuple[dict[str, Any], list[dict[str, str]]]:
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


@app.route("/admin/review", methods=["GET", "POST"])
def admin_review():
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

        return redirect(url_for("admin_review"))

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


@app.route("/admin/review/view/<record_id>")
def admin_review_view(record_id):
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
    conversation_files = utipy.list_conversation_files_in_gcs()
    conversation_ids = [
        file_name.split("/")[-1].replace(".jsonl", "")
        for file_name in conversation_files
    ]
    return render_template(
        "admin_conversations.html", conversation_ids=conversation_ids
    )


@app.route("/admin/conversations/<conversation_id>")
def admin_conversation_detail(conversation_id: str):
    if not conversation_id:
        abort(400)
    messages = utipy.get_conversation_from_gcs(conversation_id)
    if not messages:
        abort(404)
    return render_template(
        "admin_conversation_detail.html",
        conversation_id=conversation_id,
        messages=messages,
    )


@app.route("/download_chats", methods=["POST"])
def download_chats():
    if utipy.download_all():
        if "text/html" in (request.headers.get("Accept", "").lower()):
            return redirect(url_for("admin_conversations", status="downloaded"))
        return jsonify({"status": "success"}), 200
    if "text/html" in (request.headers.get("Accept", "").lower()):
        return redirect(url_for("admin_conversations", status="failed"))
    return jsonify({"status": "failure"}), 500


if __name__ == "__main__":
    if utipy.config.LOCAL:
        app.run(debug=True)
    else:
        app.run(host="0.0.0.0", port=8080, debug=False)
