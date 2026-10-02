"""Browser-facing administration and session-review routes."""

from __future__ import annotations

import csv
import hmac
import json
import logging
from pathlib import Path
from typing import Any

from flask import (
    Blueprint,
    Response,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app_support import (
    _admin_session_active,
    _normalize_review_filter,
    _require_admin_auth,
    _safe_next_path,
)
import review_sync
import session_reviews
import utilities as utipy

admin_bp = Blueprint("admin", __name__)


@admin_bp.route("/admin/login", methods=["GET", "POST"])
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


@admin_bp.route("/admin/logout", methods=["POST"])
def admin_logout():
    """End the browser admin session and redirect to the login page."""
    session.pop("admin_authenticated", None)
    return redirect(url_for("admin.admin_login"))


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
        return redirect(url_for("admin.admin_conversations", **body))
    return jsonify(body), 200


def _load_review_state_or_abort() -> list[dict[str, Any]]:
    """Load the cloud-backed review manifest or abort with a server error."""
    try:
        return session_reviews.load_review_state()
    except Exception as exc:
        abort(500, description=str(exc))


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


@admin_bp.route("/admin/review")
def admin_review():
    """Redirect the older admin review entrypoint into the cloud dashboard."""
    return redirect(url_for("admin.admin_conversations", evaluation="needs_cm_review"))


@admin_bp.route("/admin/session_settings")
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


@admin_bp.route("/review")
def review_page():
    """Render one cloud-backed review record in the browser admin UI."""
    _require_admin_auth()
    rows = _load_review_state_or_abort()
    if not rows:
        return redirect(url_for("admin.admin_conversations", status="empty"))

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


@admin_bp.post("/review/record/<int:index>/decision")
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
                "admin.admin_conversations",
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
            "admin.review_page",
            index=redirect_index,
            evaluation=dashboard_filter,
            **saved_redirect_args,
        )
    )


@admin_bp.route("/admin/review/legacy", methods=["GET", "POST"])
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

        return redirect(url_for("admin.admin_review_legacy"))

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


@admin_bp.route("/admin/review/legacy/view/<record_id>")
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


@admin_bp.route("/admin/conversations")
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


@admin_bp.get("/admin/memory_logbook/download")
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


@admin_bp.route("/admin/conversations/<conversation_id>")
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


@admin_bp.route("/admin/conversations/maintenance", methods=["POST"])
@admin_bp.route("/download_chats", methods=["POST"])
def download_chats():
    """Run non-destructive review maintenance from the admin dashboard."""
    action = (
        str(request.form.get("action", "backfill_review")).strip() or "backfill_review"
    )
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
