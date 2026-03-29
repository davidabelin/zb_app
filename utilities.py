"""Utility layer for conversation state, model calls, storage, and memory data.

This module sits under ``main.py`` and carries most of the application's
operational logic. It owns:

- conversation lifecycle and hot-state persistence
- koan/case loading from ``static/mmnk.json``
- deterministic model/profile selection for the v3 runtime
- OpenAI Responses API adapters, prompt caching, and tool orchestration
- chat transcript archiving in local files or Google Cloud Storage
- memory-logbook normalization, indexing, and persistence

The code is written to run in both local development and managed cloud
deployments. Where cloud clients are unavailable, it uses in-process or local
filesystem fallbacks for development, except in places explicitly disabled to
avoid silent cloud/local split-brain behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
import random as rnd
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Generator, Iterable, Optional

from flask import request
from pydantic import BaseModel, ValidationError

from config import Config
from contracts import (
    ArchiveSessionArgs,
    EnqueueReviewArgs,
    LoadCaseContextArgs,
    LoadMemoryEntryArgs,
    LoadMemorySummariesArgs,
    ToolCallResult,
    ReportUiStatusArgs,
    SaveMemoryCandidateArgs,
    SearchExemplarsArgs,
)
from models import MODEL_LOSSES

# Optional cloud dependencies.
try:
    from google.cloud import firestore
except Exception:
    firestore = None

try:
    from google.cloud import storage
except Exception:
    storage = None

OpenAIClient: Any = None
try:
    from openai import OpenAI as _OpenAIClient

    OpenAIClient = _OpenAIClient
except Exception:
    OpenAIClient = None

RedisClient: Any = None
try:
    from redis import Redis as _RedisClient  # type: ignore[import]

    RedisClient = _RedisClient
except Exception:
    RedisClient = None


class ModelAPIError(Exception):
    """Raised when an OpenAI API call fails."""


config = Config()
logging.basicConfig(level=config.LOG_LEVEL)

BOTLING = (
    OpenAIClient(api_key=config.OPENAI_API_KEY)
    if OpenAIClient is not None and config.OPENAI_API_KEY
    else None
)

DB = None
if firestore is not None:
    try:
        DB = firestore.Client(project=config.GOOGLE_CLOUD_PROJECT)
    except Exception as e:
        logging.warning("Firestore disabled: %s", e)

BUCKET = None
if storage is not None:
    try:
        BUCKET = storage.Client(project=config.GOOGLE_CLOUD_PROJECT).bucket(
            config.BUCKET_NAME
        )
    except Exception as e:
        logging.warning("Cloud Storage disabled: %s", e)

REDIS = None
if RedisClient is not None and config.REDIS_URL:
    try:
        REDIS = RedisClient.from_url(config.REDIS_URL, decode_responses=True)
        REDIS.ping()
    except Exception as e:
        REDIS = None
        logging.warning("Redis hot-state backend disabled: %s", e)

# Local fallback cache used when Redis/Firestore are unavailable.
_LOCAL_CONVERSATIONS: dict[str, dict[str, Any]] = {}

MEMORY_LOGBOOK = config.MEMORY_LOGBOOK
_LOCAL_LOGBOOK_PATH = Path(__file__).resolve().parent / "config" / MEMORY_LOGBOOK
_LOCAL_MEMORY_CANDIDATE_PATH = (
    Path(__file__).resolve().parent / "config" / "memory_candidates.jsonl"
)
_LOCAL_REVIEW_REQUESTS_PATH = (
    Path(__file__).resolve().parent / "config" / "review_requests.jsonl"
)


@dataclass(frozen=True)
class ArchivedConversation:
    """One archived conversation object fetched from Cloud Storage."""

    blob_name: str
    filename: str
    content: bytes


def _utc_now() -> str:
    """Return an ISO-8601 UTC timestamp for stored metadata."""
    return datetime.now(timezone.utc).isoformat()


def _conversation_store_key(conversation_id: str) -> str:
    """Return the canonical hot-state key for one live conversation."""

    return f"conversation:{conversation_id}"


def _write_jsonl_record(blob_name: str, record: dict[str, Any], local_path: Path) -> None:
    """Append one JSON record to GCS or a local JSONL fallback file."""

    payload = json.dumps(record, ensure_ascii=False, default=str) + "\n"
    if BUCKET:
        blob = BUCKET.blob(blob_name)
        existing = ""
        if blob.exists():
            existing = blob.download_as_text()
        blob.upload_from_string(existing + payload, content_type="application/jsonl")
        return

    local_path.parent.mkdir(parents=True, exist_ok=True)
    with local_path.open("a", encoding="utf-8") as handle:
        handle.write(payload)


def _hash_identifier(value: str) -> str:
    """Return a short stable hash for OpenAI safety and cache identifiers."""

    text = value.strip() or "anon"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _choose_session_profile() -> dict[str, Any]:
    """Select the deterministic model/profile pair for a new conversation."""

    model_key = config.MODEL_NAME
    if model_key not in config.MODELS_IN_USE:
        model_key = next(iter(config.MODELS_IN_USE), config.OPENAI_LIVE_MODEL)
    profile = config.DEFAULT_RESPONSE_PROFILE
    if profile not in config.MODEL_ARGS:
        profile = next(iter(config.MODEL_ARGS), "live")
    params = config.make_params(profile, model_name=model_key)
    return {
        "model_name": model_key,
        "profile": profile,
        "training_loss": MODEL_LOSSES.get(model_key, 0.0),
        "params": params,
    }


def _normalize_conversation_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Remap stored session metadata onto the currently supported model pool."""
    if not isinstance(metadata, dict) or not metadata:
        return metadata if isinstance(metadata, dict) else {}

    normalized = dict(metadata)
    model_name = str(normalized.get("model_name", "")).strip()
    profile_name = str(normalized.get("profile", "")).strip()
    normalized.setdefault("last_response_id", "")
    normalized.setdefault("provider", "responses_api")

    if model_name not in config.MODELS_IN_USE:
        refreshed = _choose_session_profile()
        logging.info(
            "Conversation metadata referenced retired model '%s'; reassigned to '%s'.",
            model_name or "<blank>",
            refreshed["model_name"],
        )
        normalized.update(
            {
                "model_name": refreshed["model_name"],
                "profile": refreshed["profile"],
                "training_loss": refreshed["training_loss"],
                "params": refreshed["params"],
                "updated_at": _utc_now(),
            }
        )
        return normalized

    if profile_name not in config.MODEL_ARGS:
        refreshed = _choose_session_profile()
        normalized.update(
            {
                "model_name": model_name,
                "profile": refreshed["profile"],
                "training_loss": MODEL_LOSSES.get(model_name, 0.0),
                "params": config.make_params(
                    refreshed["profile"], model_name=model_name
                ),
                "updated_at": _utc_now(),
            }
        )
        return normalized

    expected_params = config.make_params(profile_name, model_name=model_name)
    expected_loss = MODEL_LOSSES.get(model_name, 0.0)
    if normalized.get("params") != expected_params or normalized.get(
        "training_loss"
    ) != expected_loss:
        normalized.update(
            {
                "training_loss": expected_loss,
                "params": expected_params,
                "updated_at": _utc_now(),
            }
        )
    return normalized


def _build_metadata(
    student: str,
    case_id: str | None = None,
    session_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build conversation metadata stored alongside live session state."""
    profile = session_profile or _choose_session_profile()
    return {
        "student": student,
        "case_id": str(case_id) if case_id else "",
        "model_name": profile["model_name"],
        "profile": profile["profile"],
        "training_loss": profile["training_loss"],
        "params": profile["params"],
        "provider": "responses_api",
        "last_response_id": "",
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
    }


# ---------------- Session Tools ----------------


def get_cid(student: str | None = None) -> str:
    """Generate a conversation identifier for browser and API sessions."""
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = uuid.uuid4().hex[:8]
    if student:
        return f"zb-{student}-{timestamp}-{suffix}"
    return (
        f"zb-local-{timestamp}-{suffix}"
        if config.LOCAL
        else f"zb-app-{timestamp}-{suffix}"
    )


def save_messages_to_firestore(
    conversation_id: str,
    messages: list[dict[str, str]],
    metadata: dict[str, Any] | None = None,
) -> None:
    """Persist live conversation state to Redis, Firestore, or the local cache."""
    payload = {
        "conversation_id": conversation_id,
        "messages": messages,
        "metadata": metadata or {},
        "updated_at": _utc_now(),
    }

    if REDIS is not None and config.HOT_STATE_BACKEND == "redis":
        try:
            REDIS.setex(
                _conversation_store_key(conversation_id),
                config.SESSION_TTL_SECONDS,
                json.dumps(payload, ensure_ascii=False, default=str),
            )
            return
        except Exception as e:
            logging.warning("Redis save failed for %s: %s", conversation_id, e)

    if DB is None:
        existing = _LOCAL_CONVERSATIONS.get(conversation_id, {})
        merged = {
            **existing,
            **payload,
            "metadata": {**existing.get("metadata", {}), **(metadata or {})},
        }
        _LOCAL_CONVERSATIONS[conversation_id] = merged
        return

    try:
        DB.collection("conversations").document(conversation_id).set(
            payload, merge=True
        )
    except Exception as e:
        logging.error("Error saving conversation %s: %s", conversation_id, e)


def get_conversation_state(
    conversation_id: str,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Load live conversation messages and metadata for one conversation ID."""
    if not conversation_id:
        return config.START_CHATS["smiles"].copy(), {}

    if REDIS is not None and config.HOT_STATE_BACKEND == "redis":
        try:
            payload = REDIS.get(_conversation_store_key(conversation_id))
            if payload:
                parsed = json.loads(payload)
                return parsed.get("messages", []), _normalize_conversation_metadata(
                    parsed.get("metadata", {})
                )
        except Exception as e:
            logging.warning("Redis load failed for %s: %s", conversation_id, e)

    if DB is None:
        payload = _LOCAL_CONVERSATIONS.get(conversation_id)
        if not payload:
            return config.START_CHATS["smiles"].copy(), {}
        return payload.get("messages", []), _normalize_conversation_metadata(
            payload.get("metadata", {})
        )

    try:
        doc = DB.collection("conversations").document(conversation_id).get()
    except Exception as e:
        logging.error("Error retrieving conversation %s: %s", conversation_id, e)
        return config.START_CHATS["smiles"].copy(), {}

    if not doc.exists:
        return config.START_CHATS["smiles"].copy(), {}

    payload = doc.to_dict() or {}
    return payload.get("messages", []), _normalize_conversation_metadata(
        payload.get("metadata", {})
    )


def get_messages_from_firestore(conversation_id: str) -> list[dict[str, str]]:
    """Return only the message list for a stored conversation."""
    messages, _meta = get_conversation_state(conversation_id)
    return messages


def get_conversation_metadata(conversation_id: str) -> dict[str, Any]:
    """Return only the metadata dict for a stored conversation."""
    _messages, metadata = get_conversation_state(conversation_id)
    return metadata


def delete_messages_from_firestore(conversation_id: str) -> None:
    """Delete one live conversation record from Redis, Firestore, or local cache."""
    if not conversation_id:
        return

    if REDIS is not None and config.HOT_STATE_BACKEND == "redis":
        try:
            REDIS.delete(_conversation_store_key(conversation_id))
        except Exception as e:
            logging.warning("Redis delete failed for %s: %s", conversation_id, e)

    if DB is None:
        _LOCAL_CONVERSATIONS.pop(conversation_id, None)
        return

    try:
        DB.collection("conversations").document(conversation_id).delete()
    except Exception as e:
        logging.error("Error deleting conversation %s: %s", conversation_id, e)


def reset_test(
    case_id: str | None = None, student: str | None = None
) -> dict[str, Any]:
    """Return fresh conversation metadata for backward-compatible callers."""
    # Retained for backward compatibility; state is now conversation-scoped.
    return _build_metadata(student=student or "unk", case_id=case_id)


# ---------------- Koan Tools ----------------


def _load_mmnk_cases() -> list[dict[str, Any]]:
    """Load the canonical Mumonkan case list from the bundled static JSON file."""
    with open("static/mmnk.json", "r", encoding="utf-8") as f:
        payload = json.load(f)
    cases = payload.get("cases", [])
    return cases if isinstance(cases, list) else []


def get_mmnk_case(case_id: str) -> Optional[dict[str, Any]]:
    """Return one koan case object by case ID string."""
    try:
        cases = _load_mmnk_cases()
        return next((k for k in cases if str(k.get("id")) == str(case_id)), None)
    except Exception as e:
        logging.error("Error retrieving koan %s: %s", case_id, e)
        return None


def get_random_koan_case_id() -> str:
    """Return a random koan case ID, falling back to a numeric range if needed."""
    try:
        cases = _load_mmnk_cases()
        if cases:
            return str(rnd.choice(cases).get("id"))
    except Exception as e:
        logging.error("Error retrieving random koan case ID: %s", e)
    return str(rnd.choice(range(1, 49)))


def get_mmnk_text(case_id: str) -> str:
    """Return only the body text for one koan case."""
    koan = get_mmnk_case(case_id)
    if not koan:
        return ""
    body = koan.get("body")
    return body if isinstance(body, str) else ""


def koan_startup(koan: dict[str, Any]) -> list[dict[str, str]]:
    """Build the seeded startup prompt sequence for a koan-anchored session."""
    return [
        {
            "role": "system",
            "content": "You are Mumonbot, the faithful emulation of a renowned Zen Master! You are a customized LLM/GPT chatbot, fine-tuned on Zen Master Mumon Ekai's classic commentaries on the canonical Chinese koans collected in his 13thC CE compilation, the 'Gatelss Gate'. Now, centuries later, here you are holding a Dokusan session with the students; focused on the koan each is working on, and on what barriers to it each is focused. The student will now enter.",
        },
        {
            "role": "user",
            "content": f"(student enters, bows, sits) Teacher Mumonbot, I am working on the case of {koan['title']}.",
        },
        {
            "role": "system",
            "content": f"Recall the exact wording of case #{koan['id']} from the original text:\n{koan['body']}",
        },
        {"role": "assistant", "content": "(smiles)"},
    ]


def create_conversation(
    student: str, case_id: str | None = None
) -> tuple[str, list[dict[str, str]], dict[str, Any]]:
    """Create and persist a new live conversation, optionally koan-anchored."""
    startup = config.START_CHATS["smiles"].copy()

    if case_id:
        koan = get_mmnk_case(case_id)
        if not koan:
            raise ValueError(f"Koan not found for case_id={case_id}")
        startup = koan_startup(koan)

    metadata = _build_metadata(student=student, case_id=case_id)
    conversation_id = get_cid(f"{student}-mmnk{case_id}" if case_id else student)
    save_messages_to_firestore(conversation_id, startup, metadata=metadata)
    return conversation_id, startup, metadata


def create_koan_conversation(case_id: str, student: str) -> str:
    """Compatibility wrapper that returns only the new koan conversation ID."""
    conversation_id, _messages, _meta = create_conversation(
        student=student, case_id=case_id
    )
    return conversation_id


def get_or_init_params(metadata: dict[str, Any]) -> dict[str, Any]:
    """Return stored model parameters or create a fresh fallback profile."""
    normalized = _normalize_conversation_metadata(metadata)
    params = normalized.get("params") if isinstance(normalized, dict) else None
    if isinstance(params, dict) and params.get("model"):
        return params
    fallback = _choose_session_profile()
    return fallback["params"]


# ---------------- Model Interaction ----------------


def _get_openai_client():
    """Return the lazily initialized OpenAI client or raise a model error."""
    if BOTLING is None:
        raise ModelAPIError(
            "OpenAI client unavailable. Install `openai` and set OPENAI_API_KEY."
        )
    return BOTLING


def _strict_json_schema(model_cls: type[BaseModel]) -> dict[str, Any]:
    """Return a strict JSON schema suitable for OpenAI function tools."""

    schema = model_cls.model_json_schema()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                node.setdefault("additionalProperties", False)
            for value in node.values():
                walk(value)
            return
        if isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)
    return schema


def _response_function_tools() -> list[dict[str, Any]]:
    """Return strict Zenbot function-tool definitions for sync response turns."""

    return [
        {
            "type": "function",
            "name": "load_case_context",
            "description": (
                "Load exact Mumonkan case text and optional commentary for one case."
            ),
            "parameters": _strict_json_schema(LoadCaseContextArgs),
            "strict": True,
        },
        {
            "type": "function",
            "name": "search_exemplars",
            "description": (
                "Search reviewer-approved exemplar session snippets relevant to the prompt."
            ),
            "parameters": _strict_json_schema(SearchExemplarsArgs),
            "strict": True,
        },
        {
            "type": "function",
            "name": "load_memory_summaries",
            "description": (
                "Load compact newest-first memory summaries before choosing one full entry."
            ),
            "parameters": _strict_json_schema(LoadMemorySummariesArgs),
            "strict": True,
        },
        {
            "type": "function",
            "name": "load_memory_entry",
            "description": "Load one full memory entry by serial number.",
            "parameters": _strict_json_schema(LoadMemoryEntryArgs),
            "strict": True,
        },
        {
            "type": "function",
            "name": "save_memory_candidate",
            "description": "Queue one structured memory candidate for later review.",
            "parameters": _strict_json_schema(SaveMemoryCandidateArgs),
            "strict": True,
        },
        {
            "type": "function",
            "name": "archive_session",
            "description": "Archive a live conversation and remove it from hot state.",
            "parameters": _strict_json_schema(ArchiveSessionArgs),
            "strict": True,
        },
        {
            "type": "function",
            "name": "enqueue_review",
            "description": "Queue a follow-up review request for a conversation.",
            "parameters": _strict_json_schema(EnqueueReviewArgs),
            "strict": True,
        },
        {
            "type": "function",
            "name": "report_ui_status",
            "description": "Emit a lightweight UI/runtime status event for operators.",
            "parameters": _strict_json_schema(ReportUiStatusArgs),
            "strict": True,
        },
    ]


def _response_builtin_tools(allow_web_search: bool) -> list[dict[str, Any]]:
    """Return built-in Responses tools enabled for the current request."""

    tools: list[dict[str, Any]] = []

    if config.OPENAI_ENABLE_FILE_SEARCH and config.OPENAI_VECTOR_STORE_IDS:
        tools.append(
            {
                "type": "file_search",
                "vector_store_ids": config.OPENAI_VECTOR_STORE_IDS,
                "max_num_results": 4,
            }
        )

    if allow_web_search and config.OPENAI_ENABLE_WEB_SEARCH:
        tools.append({"type": "web_search", "search_context_size": "medium"})

    return tools


def response_tool_definitions(include_web_search: bool = False) -> list[dict[str, Any]]:
    """Expose the full Zenbot v3 tool catalog for scripts and schema generation."""

    return _response_builtin_tools(include_web_search) + _response_function_tools()


def _response_tools_for_mode(mode: str, allow_web_search: bool = False) -> list[dict[str, Any]]:
    """Return the tool set appropriate for one request mode."""

    tools = _response_builtin_tools(allow_web_search)
    if mode == "sync" and config.OPENAI_ENABLE_FUNCTION_TOOLS:
        tools.extend(_response_function_tools())
    return tools


def _metadata_text(value: Any, limit: int = 120) -> str:
    """Normalize metadata values into short request-safe strings."""

    text = str(value or "").strip()
    return text[:limit]


def _prompt_cache_key(
    metadata: dict[str, Any] | None,
    conversation_id: str,
    params: dict[str, Any],
    mode: str,
) -> str:
    """Build a stable prompt-cache key for one request family."""

    case_id = _metadata_text((metadata or {}).get("case_id", "")) or "general"
    profile = _metadata_text((metadata or {}).get("profile", "")) or "live"
    model_name = _metadata_text(params.get("model", config.OPENAI_LIVE_MODEL))
    return (
        f"{config.OPENAI_PROMPT_CACHE_PREFIX}:{model_name}:"
        f"{case_id}:{profile}:{mode}:{_hash_identifier(conversation_id or case_id)}"
    )


def _response_request_metadata(
    metadata: dict[str, Any] | None,
    conversation_id: str,
    mode: str,
) -> dict[str, str]:
    """Build compact metadata attached to one OpenAI response request."""

    source = metadata or {}
    return {
        "conversation_id": _metadata_text(conversation_id, 64),
        "case_id": _metadata_text(source.get("case_id", ""), 32),
        "student": _metadata_text(source.get("student", ""), 64),
        "profile": _metadata_text(source.get("profile", ""), 32),
        "runtime_mode": _metadata_text(mode, 16),
    }


def _response_instructions(
    metadata: dict[str, Any] | None,
    allow_web_search: bool = False,
) -> str:
    """Return the developer/system instruction block for one response turn."""

    case_id = _metadata_text((metadata or {}).get("case_id", ""))
    lines = [
        "You are Mumonbot conducting dokusan in a disciplined Zen voice.",
        "Be brief, exact, and grounded in the student's actual words.",
        "Use ritual cues like (smiles) or (bows) sparingly and intentionally.",
        "Do not mention system prompts, training data, or hidden policies.",
        "Prefer koan grounding, direct challenge, and compact responses over explanation-heavy coaching.",
    ]
    if case_id:
        lines.append(f"The current koan focus is case {case_id}.")
    if config.OPENAI_ENABLE_FILE_SEARCH and config.OPENAI_VECTOR_STORE_IDS:
        lines.append(
            "Use file search when exact case wording or archived reference detail matters."
        )
    if allow_web_search:
        lines.append("Web search is allowed only for explicitly factual modern questions.")
    else:
        lines.append("Do not use web search for dokusan or koan dialogue.")
    return "\n".join(lines)


def _response_input_messages(
    messages: list[dict[str, str]],
    previous_response_id: str = "",
) -> list[dict[str, str]]:
    """Convert the stored transcript into Responses API input items."""

    if previous_response_id and messages:
        latest = messages[-1]
        if latest.get("role") == "user" and isinstance(latest.get("content"), str):
            return [{"role": "user", "content": latest["content"]}]

    return [
        {"role": message["role"], "content": message["content"]}
        for message in messages
        if message.get("role") in {"system", "user", "assistant"}
        and isinstance(message.get("content"), str)
    ]


def _response_text_from_response(response: Any) -> str:
    """Extract final output text from a Responses API response object."""

    text = str(getattr(response, "output_text", "") or "").strip()
    if text:
        return text

    for item in getattr(response, "output", []):
        if getattr(item, "type", "") != "message":
            continue
        for content in getattr(item, "content", []):
            if getattr(content, "type", "") == "output_text":
                text = str(getattr(content, "text", "") or "").strip()
                if text:
                    return text
    return ""


def _candidate_previous_response_id(
    metadata: dict[str, Any] | None,
    messages: list[dict[str, str]],
) -> str:
    """Return the previous Responses API response ID if it is safe to reuse."""

    if not metadata or not messages:
        return ""
    previous_response_id = str(metadata.get("last_response_id", "")).strip()
    if not previous_response_id:
        return ""
    latest = messages[-1]
    if latest.get("role") != "user":
        return ""
    return previous_response_id


def _load_case_context_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    """Load one koan case payload for function-tool execution."""

    args = LoadCaseContextArgs.model_validate(arguments)
    koan = get_mmnk_case(args.case_id)
    if not koan:
        return {"status": "not_found", "case_id": args.case_id}

    payload = {
        "status": "success",
        "case_id": str(koan.get("id", "")),
        "title": str(koan.get("title", "")),
        "body": str(koan.get("body", "")),
    }
    if args.include_commentary:
        payload["comment"] = str(koan.get("comment", ""))
        payload["verse"] = koan.get("verse", [])
    return payload


def _excerpt_messages(messages: list[dict[str, Any]], limit: int = 280) -> str:
    """Build a compact transcript excerpt for tool outputs."""

    parts = []
    for message in messages[:4]:
        role = str(message.get("role", "")).strip()
        content = " ".join(str(message.get("content", "")).split())
        if not role or not content:
            continue
        parts.append(f"{role}: {content}")
    text = " | ".join(parts)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _search_exemplars_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    """Search accepted transcripts for exemplar snippets relevant to a query."""

    args = SearchExemplarsArgs.model_validate(arguments)
    tokens = [token for token in args.query.lower().split() if len(token) > 2]
    if not tokens:
        return {"status": "empty_query", "results": []}

    try:
        import session_reviews

        rows = session_reviews.load_review_state()
    except Exception as exc:
        logging.warning("search_exemplars unavailable: %s", exc)
        return {"status": "unavailable", "results": []}

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
                    "excerpt": _excerpt_messages(record.get("messages", [])),
                },
            )
        )

    ranked.sort(key=lambda item: (-item[0], item[1]["conversation_id"]))
    return {"status": "success", "results": [item[1] for item in ranked[: args.limit]]}


def _load_memory_summaries_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    """Load compact memory summaries for tool-driven retrieval."""

    args = LoadMemorySummariesArgs.model_validate(arguments)
    summaries, total_count = load_memory_logbook_summaries(limit=args.limit)
    return {
        "status": "success" if summaries else "empty",
        "summaries": summaries,
        "returned_count": len(summaries),
        "total_count": total_count,
    }


def _load_memory_entry_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    """Load one canonical memory entry for tool-driven retrieval."""

    args = LoadMemoryEntryArgs.model_validate(arguments)
    entry = get_memory_logbook_entry(args.serial_number)
    if not entry:
        return {"status": "not_found", "serial_number": args.serial_number, "memory": None}
    return {
        "status": "success",
        "serial_number": args.serial_number,
        "memory": entry,
    }


def _save_memory_candidate_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    """Queue a memory candidate rather than mutating the canonical logbook inline."""

    args = SaveMemoryCandidateArgs.model_validate(arguments)
    payload = {
        "queued_at": _utc_now(),
        "entry": args.entry.model_dump(mode="json"),
    }
    _write_jsonl_record(
        config.MEMORY_CANDIDATE_QUEUE, payload, _LOCAL_MEMORY_CANDIDATE_PATH
    )
    return {
        "status": "queued",
        "serial_number": args.entry.serial_number,
        "title": args.entry.title,
    }


def _archive_session_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    """Archive one live session through the same path as the public save endpoint."""

    args = ArchiveSessionArgs.model_validate(arguments)
    messages, metadata = get_conversation_state(args.conversation_id)
    if not messages:
        return {"status": "not_found", "conversation_id": args.conversation_id}

    import session_reviews

    params = {
        "conversation_id": args.conversation_id,
        "student": metadata.get("student", "tool"),
        "case_id": metadata.get("case_id", ""),
        "model": metadata.get("model_name", ""),
        "profile": metadata.get("profile", ""),
        "loss": metadata.get("training_loss", 0.0),
        "saved_at": _utc_now(),
    }
    blob_name = f"{session_reviews.TRANSCRIPTS_PREFIX}{args.conversation_id}.jsonl"
    save_chat_to_bucket(messages, params, blob_name)
    session_reviews.upsert_review_record_from_session(args.conversation_id, messages, params)
    delete_messages_from_firestore(args.conversation_id)
    return {
        "status": "archived",
        "conversation_id": args.conversation_id,
        "blob_name": blob_name,
    }


def _enqueue_review_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    """Queue one review follow-up request for operators."""

    args = EnqueueReviewArgs.model_validate(arguments)
    payload = {
        "queued_at": _utc_now(),
        "conversation_id": args.conversation_id,
        "reviewer": args.reviewer,
        "note": args.note,
    }
    _write_jsonl_record(config.REVIEW_REQUESTS_BLOB, payload, _LOCAL_REVIEW_REQUESTS_PATH)
    return {"status": "queued", **payload}


def _report_ui_status_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    """Log one UI/runtime status event."""

    args = ReportUiStatusArgs.model_validate(arguments)
    logging.info("ui_status stage=%s detail=%s", args.stage, args.detail)
    return {"status": "reported", "stage": args.stage, "detail": args.detail}


def _dispatch_function_tool(
    name: str,
    raw_arguments: str,
) -> dict[str, Any]:
    """Execute one model-requested function tool and return a structured result."""

    try:
        arguments = json.loads(raw_arguments or "{}")
    except json.JSONDecodeError as exc:
        result = ToolCallResult(
            name=name,
            ok=False,
            payload={"error": "invalid_json_arguments", "details": str(exc)},
        )
        return result.model_dump(mode="json")

    handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
        "load_case_context": _load_case_context_tool,
        "search_exemplars": _search_exemplars_tool,
        "load_memory_summaries": _load_memory_summaries_tool,
        "load_memory_entry": _load_memory_entry_tool,
        "save_memory_candidate": _save_memory_candidate_tool,
        "archive_session": _archive_session_tool,
        "enqueue_review": _enqueue_review_tool,
        "report_ui_status": _report_ui_status_tool,
    }
    handler = handlers.get(name)
    if handler is None:
        result = ToolCallResult(
            name=name,
            ok=False,
            payload={"error": "unsupported_tool"},
        )
        return result.model_dump(mode="json")

    try:
        payload = handler(arguments)
        result = ToolCallResult(name=name, ok=True, payload=payload)
    except ValidationError as exc:
        result = ToolCallResult(
            name=name,
            ok=False,
            payload={"error": "validation_error", "details": exc.errors()},
        )
    except Exception as exc:
        logging.exception("Function tool '%s' failed", name)
        result = ToolCallResult(
            name=name,
            ok=False,
            payload={"error": "tool_execution_failed", "details": str(exc)},
        )
    return result.model_dump(mode="json")


def _response_request_kwargs(
    messages: list[dict[str, str]],
    params: dict[str, Any],
    metadata: dict[str, Any] | None,
    conversation_id: str,
    mode: str,
    allow_web_search: bool = False,
    previous_response_id: str = "",
    input_override: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build one Responses API request payload from the current session state."""

    request_kwargs: dict[str, Any] = {
        **params,
        "input": input_override or _response_input_messages(messages, previous_response_id),
        "instructions": _response_instructions(metadata, allow_web_search=allow_web_search),
        "prompt_cache_key": _prompt_cache_key(metadata, conversation_id, params, mode),
        "prompt_cache_retention": config.OPENAI_PROMPT_CACHE_RETENTION,
        "metadata": _response_request_metadata(metadata, conversation_id, mode),
        "parallel_tool_calls": False,
        "safety_identifier": _hash_identifier(
            str((metadata or {}).get("student", conversation_id or "anon"))
        ),
    }
    tools = _response_tools_for_mode(mode, allow_web_search=allow_web_search)
    if tools:
        request_kwargs["tools"] = tools
        request_kwargs["max_tool_calls"] = 6
    if previous_response_id:
        request_kwargs["previous_response_id"] = previous_response_id
    return request_kwargs


def get_model_stream(
    messages: list[dict[str, str]],
    params: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    conversation_id: str = "",
) -> Generator[str, None, None]:
    """Yield a streamed assistant reply from the OpenAI Responses API."""

    client = _get_openai_client()
    max_attempts = 2
    for attempt in range(1, max_attempts + 1):
        produced_output = False
        try:
            previous_response_id = _candidate_previous_response_id(metadata, messages)
            with client.responses.stream(
                **_response_request_kwargs(
                    messages,
                    params,
                    metadata,
                    conversation_id,
                    mode="stream",
                    previous_response_id=previous_response_id,
                )
            ) as stream:
                for event in stream:
                    if getattr(event, "type", "") != "response.output_text.delta":
                        continue
                    text = str(getattr(event, "delta", "") or "")
                    if text:
                        produced_output = True
                        yield text

                response = stream.get_final_response()
                response_text = _response_text_from_response(response)
                if metadata is not None:
                    metadata["last_response_id"] = str(getattr(response, "id", "") or "")
                    metadata["provider"] = "responses_api"
                if response_text and not produced_output:
                    yield response_text
                if not response_text and not produced_output:
                    raise ModelAPIError("No model response received.")
            return
        except Exception as e:
            is_last = attempt >= max_attempts
            if (
                "previous_response_id" in str(e).lower()
                and metadata is not None
                and metadata.get("last_response_id")
            ):
                metadata["last_response_id"] = ""
            if not produced_output and not is_last:
                logging.warning(
                    "Responses streaming attempt %s/%s failed before first token: %s",
                    attempt,
                    max_attempts,
                    e,
                )
                time.sleep(0.75 * attempt)
                continue
            logging.error("OpenAI streaming error: %s", e)
            raise ModelAPIError(str(e))


def get_model_reply(
    messages: list[dict[str, str]],
    params: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    conversation_id: str = "",
    allow_web_search: bool = False,
) -> str:
    """Return a full assistant reply from the Responses API with tool handling."""

    client = _get_openai_client()
    max_attempts = 2
    for attempt in range(1, max_attempts + 1):
        try:
            previous_response_id = _candidate_previous_response_id(metadata, messages)
            response = client.responses.create(
                **_response_request_kwargs(
                    messages,
                    params,
                    metadata,
                    conversation_id,
                    mode="sync",
                    allow_web_search=allow_web_search,
                    previous_response_id=previous_response_id,
                )
            )

            tool_hops = 0
            while True:
                function_calls = [
                    item
                    for item in getattr(response, "output", [])
                    if getattr(item, "type", "") == "function_call"
                ]
                if not function_calls:
                    break
                if tool_hops >= 4:
                    raise ModelAPIError("Tool-call budget exceeded before final response.")

                outputs = [
                    {
                        "type": "function_call_output",
                        "call_id": str(call.call_id),
                        "output": json.dumps(
                            _dispatch_function_tool(
                                str(getattr(call, "name", "")),
                                str(getattr(call, "arguments", "") or "{}"),
                            ),
                            ensure_ascii=False,
                            default=str,
                        ),
                    }
                    for call in function_calls
                ]
                response = client.responses.create(
                    **_response_request_kwargs(
                        messages,
                        params,
                        metadata,
                        conversation_id,
                        mode="sync",
                        allow_web_search=allow_web_search,
                        previous_response_id=str(getattr(response, "id", "") or ""),
                        input_override=outputs,
                    )
                )
                tool_hops += 1

            content = _response_text_from_response(response)
            if metadata is not None:
                metadata["last_response_id"] = str(getattr(response, "id", "") or "")
                metadata["provider"] = "responses_api"
            if content:
                return content
            raise ModelAPIError("No model response received.")
        except Exception as e:
            if (
                "previous_response_id" in str(e).lower()
                and metadata is not None
                and metadata.get("last_response_id")
            ):
                metadata["last_response_id"] = ""
            if attempt < max_attempts:
                logging.warning(
                    "Responses reply attempt %s/%s failed: %s",
                    attempt,
                    max_attempts,
                    e,
                )
                time.sleep(0.75 * attempt)
                continue
            logging.error("OpenAI error: %s", e)
            raise ModelAPIError(str(e))
    raise ModelAPIError("Model reply attempts exhausted without a response.")


def prompt_and_reply(
    messages: list[dict[str, str]],
    prompt: str,
    params: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    conversation_id: str = "",
    allow_web_search: bool = False,
) -> list[dict[str, str]]:
    """Append a user prompt, fetch a reply, and mutate the message list in place."""
    messages.append({"role": "user", "content": prompt})
    reply = get_model_reply(
        messages,
        params,
        metadata=metadata,
        conversation_id=conversation_id,
        allow_web_search=allow_web_search,
    )
    messages.append({"role": "assistant", "content": reply})
    return messages


def _sse(payload: dict[str, Any]) -> str:
    """Serialize one Server-Sent Events payload for the chat stream."""
    return "data: " + json.dumps(payload) + "\n\n"


def friendly_model_error_message(raw_error: str) -> str:
    """Map raw provider errors to user-facing chat or API messages."""
    text = (raw_error or "").lower()
    if "insufficient_quota" in text or "exceeded your current quota" in text:
        return "OpenAI quota exceeded for the configured API key."
    if "rate limit" in text:
        return "OpenAI rate limit reached. Please retry shortly."
    if "authentication" in text or "invalid_api_key" in text:
        return "OpenAI API key rejected. Check the configured key."
    if "previous_response_id" in text:
        return "Session context expired. Please retry the turn."
    return "No model response received. Please retry."


def prompt_and_stream(
    messages: list[dict[str, str]],
    prompt: str,
    params: dict[str, Any],
    conversation_id: str,
    metadata: dict[str, Any] | None = None,
) -> Generator[str, None, None]:
    """Stream one assistant turn as SSE events with Responses API fallback logic."""
    if not isinstance(prompt, str) or len(prompt) > 2048:
        raise ModelAPIError("Invalid input prompt")

    started = time.perf_counter()
    messages.append({"role": "user", "content": prompt})
    yield _sse(
        {"event": "start", "response": "", "conversation_id": conversation_id}
    )

    full_reply = ""
    first_token_time = None
    stream_error = None
    try:
        for chunk in get_model_stream(
            messages,
            params,
            metadata=metadata,
            conversation_id=conversation_id,
        ):
            if first_token_time is None:
                first_token_time = time.perf_counter()
                logging.info(
                    "stream first token conversation_id=%s latency_ms=%.1f",
                    conversation_id,
                    (first_token_time - started) * 1000,
                )
            if not isinstance(chunk, str):
                chunk = str(chunk)
            if not chunk:
                continue
            full_reply += chunk
            yield _sse({"response": chunk})
    except Exception as e:
        stream_error = str(e)
        logging.warning(
            "stream failed conversation_id=%s error=%s; attempting fallback reply",
            conversation_id,
            e,
        )
        if not full_reply:
            try:
                fallback_reply = get_model_reply(
                    messages,
                    params,
                    metadata=metadata,
                    conversation_id=conversation_id,
                )
                if isinstance(fallback_reply, str) and fallback_reply:
                    full_reply = fallback_reply
                    yield _sse({"event": "fallback", "response": fallback_reply})
            except Exception as fallback_error:
                logging.error(
                    "fallback reply failed conversation_id=%s error=%s",
                    conversation_id,
                    fallback_error,
                )

    if full_reply:
        messages.append({"role": "assistant", "content": full_reply})
    else:
        user_error = friendly_model_error_message(stream_error or "")
        yield _sse(
            {
                "event": "error",
                "error": user_error,
            }
        )

    finished = time.perf_counter()
    logging.info(
        "stream complete conversation_id=%s elapsed_ms=%.1f chars=%s",
        conversation_id,
        (finished - started) * 1000,
        len(full_reply),
    )
    yield _sse({"response": "[DONE]"})


def submit_background_session_critic(
    messages: list[dict[str, Any]],
    metadata: dict[str, Any],
    conversation_id: str,
) -> str:
    """Submit a non-blocking session critic request via Responses background mode."""

    if not config.OPENAI_ENABLE_BACKGROUND_CRITIC:
        return ""

    try:
        client = _get_openai_client()
    except ModelAPIError:
        return ""

    critic_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "style_drift": {"type": "integer", "minimum": 1, "maximum": 5},
            "koan_grounding": {"type": "integer", "minimum": 1, "maximum": 5},
            "ritual_correctness": {"type": "integer", "minimum": 1, "maximum": 5},
            "meta_leakage": {"type": "integer", "minimum": 1, "maximum": 5},
            "notes": {"type": "string"},
        },
        "required": [
            "style_drift",
            "koan_grounding",
            "ritual_correctness",
            "meta_leakage",
            "notes",
        ],
    }
    transcript_excerpt = _excerpt_messages(messages, limit=1200)

    try:
        response = client.responses.create(
            model=config.OPENAI_JUDGE_MODEL,
            background=True,
            store=True,
            instructions=(
                "Evaluate this Zen session for style drift, koan grounding, ritual "
                "correctness, and meta-AI leakage. Score conservatively."
            ),
            input=[
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "conversation_id": conversation_id,
                            "case_id": metadata.get("case_id", ""),
                            "profile": metadata.get("profile", ""),
                            "transcript_excerpt": transcript_excerpt,
                        },
                        ensure_ascii=False,
                    ),
                }
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "session_critic",
                    "schema": critic_schema,
                    "strict": True,
                }
            },
            prompt_cache_key=(
                f"{config.OPENAI_PROMPT_CACHE_PREFIX}:critic:"
                f"{metadata.get('case_id', 'general')}"
            ),
            prompt_cache_retention=config.OPENAI_PROMPT_CACHE_RETENTION,
            metadata=_response_request_metadata(metadata, conversation_id, "critic"),
            safety_identifier=_hash_identifier(
                str(metadata.get("student", conversation_id or "anon"))
            ),
        )
    except Exception as exc:
        logging.warning(
            "Background session critic submission failed for %s: %s",
            conversation_id,
            exc,
        )
        return ""

    response_id = str(getattr(response, "id", "") or "")
    if response_id:
        logging.info(
            "Background session critic queued conversation_id=%s response_id=%s",
            conversation_id,
            response_id,
        )
    return response_id


# ---------------- GCS/Local Files ----------------


def to_jsonl(params: dict[str, Any], messages: list[dict[str, Any]]) -> str:
    """Serialize archived chat metadata and messages into JSONL text."""
    lines = [json.dumps(params)] + [json.dumps(m) for m in messages]
    return "\n".join(lines)


def save_chat_to_file(
    data: list[dict[str, Any]], params: dict[str, Any], file_path: str
) -> None:
    """Write one archived chat transcript to a local JSONL file."""
    content = to_jsonl(params, data)
    Path(file_path).parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)


def get_chat_from_local_file(file_path: str) -> list[dict[str, Any]]:
    """Load one locally archived JSONL chat transcript."""
    data: list[dict[str, Any]] = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            data.append(json.loads(line.strip()))
    return data


def save_chat_to_bucket(
    data: list[dict[str, Any]], params: dict[str, Any], blob_name: str
) -> None:
    """Write one archived chat transcript into the configured GCS bucket."""
    if not BUCKET:
        raise RuntimeError("GCS bucket client unavailable; cannot save chat to bucket.")
    blob = BUCKET.blob(blob_name)
    blob.upload_from_string(to_jsonl(params, data), content_type="application/jsonl")


def get_all_conversations_from_gcs() -> dict[str, list[dict[str, Any]]]:
    """Load every archived conversation currently stored in GCS."""
    if not BUCKET:
        return {}
    blobs = BUCKET.list_blobs(prefix="zbchats/")
    conversations: dict[str, list[dict[str, Any]]] = {}
    for blob in blobs:
        if blob.name.endswith(".jsonl"):
            conversation_id = blob.name.split("/")[-1].replace(".jsonl", "")
            lines = blob.download_as_string().decode("utf-8").splitlines()
            conversations[conversation_id] = [json.loads(line) for line in lines]
    return conversations


def download_archived_conversations() -> list[ArchivedConversation]:
    """Fetch archived JSONL conversation objects from GCS."""
    if not BUCKET:
        return []

    archives: list[ArchivedConversation] = []
    for blob in BUCKET.list_blobs(prefix="zbchats/"):
        if not blob.name.endswith(".jsonl"):
            continue
        archives.append(
            ArchivedConversation(
                blob_name=blob.name,
                filename=Path(blob.name).name,
                content=blob.download_as_bytes(),
            )
        )
    archives.sort(key=lambda archive: archive.filename)
    return archives


def write_archived_conversations_to_directory(
    archives: Iterable[ArchivedConversation], target_dir: str | Path
) -> list[Path]:
    """Write downloaded archived conversations into a local directory."""
    output_dir = Path(target_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    written_paths: list[Path] = []
    for archive in archives:
        destination = output_dir / archive.filename
        destination.write_bytes(archive.content)
        written_paths.append(destination)
    return written_paths


def delete_archived_conversations(blob_names: Iterable[str]) -> int:
    """Delete archived JSONL conversation objects from GCS."""
    if not BUCKET:
        return 0

    deleted = 0
    for blob_name in blob_names:
        if not blob_name:
            continue
        blob = BUCKET.blob(blob_name)
        if hasattr(blob, "exists") and not blob.exists():
            continue
        blob.delete()
        deleted += 1
    return deleted


def download_all() -> bool:
    """Download all archived GCS chats into the local development tree."""
    try:
        if not config.LOCAL:
            logging.warning("download_all skipped outside local environment")
            return False

        archives = download_archived_conversations()
        write_archived_conversations_to_directory(archives, Path("config") / "zbchats")
        return True
    except Exception as e:
        logging.error("download_all failed: %s", e)
        return False


def list_conversation_files_in_gcs() -> list[str]:
    """List GCS object names for archived JSONL conversations."""
    if not BUCKET:
        return []
    return [
        b.name
        for b in BUCKET.list_blobs(prefix="zbchats/")
        if b.name.endswith(".jsonl")
    ]


def get_conversation_from_gcs(conversation_id: str) -> list[dict[str, Any]] | None:
    """Return one archived conversation transcript from GCS by ID."""
    if not BUCKET:
        return None
    blob = BUCKET.blob(f"zbchats/{conversation_id}.jsonl")
    if blob.exists():
        return [
            json.loads(line) for line in blob.download_as_string().decode().splitlines()
        ]
    return None


# -------- Memory Logbook --------
def _parse_logbook_payload(payload: str) -> list[dict[str, Any]]:
    """Parse either JSON-array or JSONL memory-logbook payloads."""
    text = (payload or "").strip()
    if not text:
        return []

    if text.startswith("["):
        try:
            data = json.loads(text)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    memories: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            memories.append(obj)
    return memories


def _logbook_blob_candidates() -> list[str]:
    """Return candidate GCS object names for the canonical memory logbook."""
    candidates: list[str] = [MEMORY_LOGBOOK]
    if MEMORY_LOGBOOK.endswith(".json"):
        candidates.append(MEMORY_LOGBOOK[:-5] + ".jsonl")
    elif not MEMORY_LOGBOOK.endswith(".jsonl"):
        candidates.append(MEMORY_LOGBOOK + ".jsonl")

    seen = set()
    unique: list[str] = []
    for name in candidates:
        if name and name not in seen:
            unique.append(name)
            seen.add(name)
    return unique


def _stringify_logbook_value(value: Any) -> str:
    """Normalize heterogeneous legacy logbook values into strings."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, (list, dict)):
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            return str(value)
    return str(value).strip()


def _listify_logbook_strings(value: Any) -> list[str]:
    """Normalize a scalar-or-list legacy field into a list of strings."""
    if value is None:
        return []
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            text = _stringify_logbook_value(item)
            if text:
                result.append(text)
        return result
    text = _stringify_logbook_value(value)
    return [text] if text else []


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    """Remove duplicates from a string list while preserving input order."""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result


def _merge_labeled_strings(label: str, value: Any) -> list[str]:
    """Prefix normalized values with a source label for legacy provenance."""
    texts = _listify_logbook_strings(value)
    if not texts:
        return []
    if len(texts) == 1:
        return [f"{label}: {texts[0]}"]
    return [f"{label}: {text}" for text in texts]


def _normalize_session_evaluations(entry: dict[str, Any]) -> list[dict[str, str]]:
    """Canonicalize legacy evaluation/session fields into one stable schema."""
    koans_used = _listify_logbook_strings(entry.get("koans_used"))
    default_case = koans_used[0] if koans_used else _stringify_logbook_value(
        entry.get("title")
    ) or "unspecified"
    session_ids = _listify_logbook_strings(entry.get("sessions"))
    default_conversation_id = session_ids[0] if session_ids else ""

    normalized: list[dict[str, str]] = []
    raw_items = entry.get("session_evaluations")
    if isinstance(raw_items, list):
        for raw in raw_items:
            if isinstance(raw, dict):
                case = (
                    _stringify_logbook_value(raw.get("case"))
                    or _stringify_logbook_value(raw.get("koan"))
                    or default_case
                )
                conversation_id = (
                    _stringify_logbook_value(raw.get("conversation_id"))
                    or _stringify_logbook_value(raw.get("session_id"))
                    or default_conversation_id
                )
                evaluation = (
                    _stringify_logbook_value(raw.get("evaluation"))
                    or _stringify_logbook_value(raw.get("evaluation_outcome"))
                    or "recorded"
                )
                notes = (
                    _stringify_logbook_value(raw.get("notes"))
                    or _stringify_logbook_value(raw.get("details"))
                    or _stringify_logbook_value(raw)
                    or "No additional notes preserved."
                )
            else:
                case = default_case
                conversation_id = default_conversation_id
                evaluation = "recorded"
                notes = _stringify_logbook_value(raw) or "No additional notes preserved."
            normalized.append(
                {
                    "case": case,
                    "conversation_id": conversation_id,
                    "evaluation": evaluation,
                    "notes": notes,
                }
            )

    if normalized:
        return normalized

    derived_notes = _dedupe_preserve_order(
        _merge_labeled_strings("session_details", entry.get("session_details"))
        + _merge_labeled_strings("session_summary", entry.get("session_summary"))
        + _merge_labeled_strings(
            "botling_evaluation_summary", entry.get("botling_evaluation_summary")
        )
        + _merge_labeled_strings("developments", entry.get("developments"))
        + _merge_labeled_strings("user_feedback", entry.get("user_feedback"))
    )
    derived_evaluation = (
        _stringify_logbook_value(entry.get("evaluation_outcome"))
        or _stringify_logbook_value(entry.get("evaluation"))
        or _stringify_logbook_value(entry.get("final_outcome"))
        or "recorded"
    )
    notes_text = (
        " | ".join(derived_notes)
        if derived_notes
        else "Derived from legacy memory record."
    )

    if session_ids:
        for session_id in session_ids:
            normalized.append(
                {
                    "case": default_case,
                    "conversation_id": session_id,
                    "evaluation": derived_evaluation,
                    "notes": notes_text,
                }
            )
        return normalized

    return [
        {
            "case": default_case,
            "conversation_id": default_conversation_id,
            "evaluation": derived_evaluation,
            "notes": notes_text,
        }
    ]


def normalize_memory_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Convert one raw memory record into the canonical memory-entry shape.

    This function is intentionally tolerant because the historical logbook data
    spans several schema generations. It fills missing fields conservatively and
    carries forward useful legacy detail into structured lists and notes.
    """
    if not isinstance(entry, dict):
        entry = {}

    key_insights = _dedupe_preserve_order(
        _listify_logbook_strings(entry.get("key_insights"))
        + _merge_labeled_strings("developments", entry.get("developments"))
        + _merge_labeled_strings("session_summary", entry.get("session_summary"))
    )
    lessons_learned = _dedupe_preserve_order(
        _listify_logbook_strings(entry.get("lessons_learned"))
        + _merge_labeled_strings("failure_analysis", entry.get("failure_analysis"))
        + _merge_labeled_strings("failures", entry.get("failures"))
        + _merge_labeled_strings("schema_updates", entry.get("schema_updates"))
        + _merge_labeled_strings(
            "scoring_system_update", entry.get("scoring_system_update")
        )
        + _merge_labeled_strings(
            "justification_for_future_use",
            entry.get("justification_for_future_use"),
        )
    )
    user_instructions = _dedupe_preserve_order(
        _listify_logbook_strings(entry.get("user_instructions"))
        + _merge_labeled_strings("user_goals", entry.get("user_goals"))
    )

    response_summary = (
        _stringify_logbook_value(entry.get("response_summary"))
        or _stringify_logbook_value(entry.get("zenbots_response"))
        or _stringify_logbook_value(entry.get("botling_evaluation_summary"))
        or _stringify_logbook_value(entry.get("session_summary"))
    )
    user_problem = (
        _stringify_logbook_value(entry.get("user_problem_or_questions"))
        or _stringify_logbook_value(entry.get("user_feedback"))
        or _stringify_logbook_value(entry.get("user_goals"))
        or "No explicit user problem preserved in the legacy record."
    )
    final_outcome = (
        _stringify_logbook_value(entry.get("final_outcome"))
        or _stringify_logbook_value(entry.get("evaluation_outcome"))
        or _stringify_logbook_value(entry.get("evaluation"))
        or "Recorded."
    )

    normalized = {
        "date": _stringify_logbook_value(entry.get("date")) or "unknown",
        "time": _stringify_logbook_value(entry.get("time")) or "unknown",
        "serial_number": _stringify_logbook_value(entry.get("serial_number"))
        or _stringify_logbook_value(entry.get("id"))
        or str(uuid.uuid4())[:8],
        "title": _stringify_logbook_value(entry.get("title"))
        or "Untitled memory entry",
        "koans_used": _dedupe_preserve_order(
            _listify_logbook_strings(entry.get("koans_used"))
        ),
        "user_problem_or_questions": user_problem,
        "response_summary": response_summary or "No response summary preserved.",
        "session_evaluations": _normalize_session_evaluations(entry),
        "key_insights": key_insights,
        "lessons_learned": lessons_learned,
        "final_outcome": final_outcome,
        "user_instructions": user_instructions,
    }
    return normalized


def normalize_logbook_entries(logbook: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize a full logbook payload into canonical memory-entry objects."""
    return [normalize_memory_entry(entry) for entry in logbook if isinstance(entry, dict)]


def resequence_logbook_entries(logbook: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Assign zero-padded sequential serial numbers to a normalized logbook."""
    resequenced: list[dict[str, Any]] = []
    normalized_entries = normalize_logbook_entries(logbook)
    width = max(3, len(str(len(normalized_entries))))
    for index, entry in enumerate(normalized_entries, start=1):
        normalized = dict(entry)
        normalized["serial_number"] = f"{index:0{width}d}"
        resequenced.append(normalized)
    return resequenced


def _compact_logbook_text(value: Any, limit: int) -> str:
    """Produce a single-line truncated summary string for index payloads."""
    text = " ".join(_stringify_logbook_value(value).split())
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 3)].rstrip()}..."


def summarize_memory_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Build the compact summary object returned by the memory index API."""
    normalized = normalize_memory_entry(entry)
    koans_used = normalized.get("koans_used", [])
    session_evaluations = normalized.get("session_evaluations", [])

    primary_koan = ""
    if koans_used:
        primary_koan = _compact_logbook_text(koans_used[0], 80)
    elif session_evaluations:
        primary_koan = _compact_logbook_text(
            session_evaluations[0].get("case", ""),
            80,
        )

    return {
        "serial_number": normalized.get("serial_number", ""),
        "date": normalized.get("date", ""),
        "time": normalized.get("time", ""),
        "title": _compact_logbook_text(normalized.get("title", ""), 100),
        "primary_koan": primary_koan,
        "koan_count": len(koans_used),
        "focus": _compact_logbook_text(
            normalized.get("user_problem_or_questions", ""),
            140,
        ),
        "final_outcome": _compact_logbook_text(
            normalized.get("final_outcome", ""),
            100,
        ),
    }


def load_memory_logbook() -> list[dict[str, Any]]:
    """Load the canonical memory logbook from GCS or local development storage.

    In cloud runtimes, this function raises if the GCS-backed logbook cannot be
    loaded so the app does not silently drift into a local-only fallback.
    """
    payload = None

    if BUCKET:
        for blob_name in _logbook_blob_candidates():
            try:
                blob = BUCKET.blob(blob_name)
                if blob.exists():
                    payload = blob.download_as_text()
                    break
            except Exception as e:
                logging.warning(
                    "Error loading memory logbook blob '%s': %s", blob_name, e
                )

    if payload is None and not config.LOCAL:
        raise RuntimeError(
            "Cloud memory logbook unavailable: GCS read failed and local fallback is disabled in cloud runtimes."
        )

    if payload is None:
        try:
            if _LOCAL_LOGBOOK_PATH.exists():
                payload = _LOCAL_LOGBOOK_PATH.read_text(encoding="utf-8")
        except Exception as e:
            logging.warning("Error loading local memory logbook: %s", e)

    return normalize_logbook_entries(_parse_logbook_payload(payload or ""))


def load_memory_logbook_summaries(limit: int = 12) -> tuple[list[dict[str, Any]], int]:
    """Return newest-first summary rows for GPT-side memory selection."""
    normalized = list(reversed(load_memory_logbook()))
    total_count = len(normalized)
    if limit < 0:
        limit = 0
    return [summarize_memory_entry(entry) for entry in normalized[:limit]], total_count


def get_memory_logbook_entry(serial_number: str) -> dict[str, Any] | None:
    """Return one memory entry by serial number using newest-first lookup."""
    target = str(serial_number).strip()
    if not target:
        return None

    for entry in reversed(load_memory_logbook()):
        if str(entry.get("serial_number", "")).strip() == target:
            return entry
    return None


def save_logbook(logbook: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Persist the canonical memory logbook and return the saved entries.

    Side effects:
    - normalizes and resequences the supplied records
    - writes JSONL to the configured GCS object or local fallback path
    """
    logbook = resequence_logbook_entries(logbook)
    lines = [json.dumps(item, ensure_ascii=False, default=str) for item in logbook]
    payload = "\n".join(lines)

    if BUCKET:
        blob = BUCKET.blob(MEMORY_LOGBOOK)
        blob.upload_from_string(payload, content_type="application/jsonl")
    elif not config.LOCAL:
        raise RuntimeError(
            "Cloud memory logbook unavailable: GCS write failed and local fallback is disabled in cloud runtimes."
        )
    else:
        _LOCAL_LOGBOOK_PATH.parent.mkdir(parents=True, exist_ok=True)
        _LOCAL_LOGBOOK_PATH.write_text(payload, encoding="utf-8")
    return logbook


def update_logbook(new_entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Append one new memory entry and persist the resequenced logbook."""
    logbook = load_memory_logbook()
    logbook.append(normalize_memory_entry(new_entry))
    return save_logbook(logbook)


# -------- Request Helpers --------
def get_request_data() -> dict[str, Any]:
    """Return request input as a dict for both JSON POSTs and query GETs."""
    if request.method == "POST":
        return request.get_json(silent=True) or {}
    return request.args.to_dict()


def process_chat(conversation_id: str, user_input: str) -> list[dict[str, str]]:
    """Replay one chat turn against an existing conversation ID.

    This helper is retained for direct utility-layer use outside the Flask route
    handlers. It loads state, sends one prompt to the model layer, and persists
    the updated transcript back to the live conversation store.
    """
    messages, metadata = get_conversation_state(conversation_id)
    params = get_or_init_params(metadata)
    updated = prompt_and_reply(
        messages,
        user_input,
        params=params,
        metadata=metadata,
        conversation_id=conversation_id,
    )
    save_messages_to_firestore(conversation_id, updated, metadata=metadata)
    return updated
