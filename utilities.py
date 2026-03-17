"""Utility layer for conversation state, model calls, storage, and memory data.

This module sits under ``main.py`` and carries most of the app's operational
logic. It owns:

- conversation lifecycle and Firestore persistence
- koan/case loading from ``static/mmnk.json``
- randomized model/profile selection
- OpenAI chat completion and streaming adapters
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
import json
import logging
import os
import random as rnd
import time
import uuid
from pathlib import Path
from typing import Any, Generator, Iterable, Optional

from flask import request

from config import Config
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

try:
    import openai
    from openai import OpenAI
except Exception:
    openai = None
    OpenAI = None


class ModelAPIError(Exception):
    """Raised when an OpenAI API call fails."""


config = Config()
logging.basicConfig(level=config.LOG_LEVEL)

BOTLING = (
    OpenAI(api_key=config.OPENAI_API_KEY) if OpenAI and config.OPENAI_API_KEY else None
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

# Local fallback cache used when Firestore is unavailable.
_LOCAL_CONVERSATIONS: dict[str, dict[str, Any]] = {}

MEMORY_LOGBOOK = config.MEMORY_LOGBOOK
_LOCAL_LOGBOOK_PATH = Path(__file__).resolve().parent / "config" / MEMORY_LOGBOOK


@dataclass(frozen=True)
class ArchivedConversation:
    """One archived conversation object fetched from Cloud Storage."""

    blob_name: str
    filename: str
    content: bytes


def _utc_now() -> str:
    """Return an ISO-8601 UTC timestamp for stored metadata."""
    return datetime.now(timezone.utc).isoformat()


def _choose_session_profile() -> dict[str, Any]:
    """Select a random model/profile pair for a new conversation."""
    model_key = rnd.choice(list(config.MODELS.keys()))
    profile = rnd.choice(list(config.MODEL_ARGS.keys()))
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

    if model_name not in config.MODELS:
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
    """Persist live conversation state to Firestore or the local fallback cache."""
    payload = {
        "conversation_id": conversation_id,
        "messages": messages,
        "metadata": metadata or {},
        "updated_at": _utc_now(),
    }

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
    """Delete one live conversation record from Firestore or local cache."""
    if not conversation_id:
        return

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
    if BOTLING is None or openai is None:
        raise ModelAPIError(
            "OpenAI client unavailable. Install `openai` and set OPENAI_API_KEY."
        )
    return BOTLING


def get_model_stream(
    messages: list[dict[str, str]],
    params: dict[str, Any],
) -> Generator[str, None, None]:
    """Yield a streamed assistant reply from OpenAI with a small retry budget."""
    client = _get_openai_client()
    max_attempts = 2
    for attempt in range(1, max_attempts + 1):
        produced_output = False
        try:
            stream = client.chat.completions.create(
                messages=messages, stream=True, **params
            )
            for chunk in stream:
                text = chunk.choices[0].delta.content
                if text:
                    produced_output = True
                    yield text
            return
        except Exception as e:
            is_last = attempt >= max_attempts
            if not produced_output and not is_last:
                logging.warning(
                    "OpenAI streaming attempt %s/%s failed before first token: %s",
                    attempt,
                    max_attempts,
                    e,
                )
                time.sleep(0.75 * attempt)
                continue
            logging.error("OpenAI streaming error: %s", e)
            raise ModelAPIError(str(e))


def get_model_reply(messages: list[dict[str, str]], params: dict[str, Any]) -> str:
    """Return a full assistant reply from OpenAI with retry-once behavior."""
    client = _get_openai_client()
    max_attempts = 2
    for attempt in range(1, max_attempts + 1):
        try:
            completion = client.chat.completions.create(messages=messages, **params)
            content = completion.choices[0].message.content
            if content:
                return content
            raise ModelAPIError("No model response received.")
        except Exception as e:
            if attempt < max_attempts:
                logging.warning(
                    "OpenAI reply attempt %s/%s failed: %s",
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
) -> list[dict[str, str]]:
    """Append a user prompt, fetch a reply, and mutate the message list in place."""
    messages.append({"role": "user", "content": prompt})
    reply = get_model_reply(messages, params)
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
    return "No model response received. Please retry."


def prompt_and_stream(
    messages: list[dict[str, str]],
    prompt: str,
    params: dict[str, Any],
    conversation_id: str,
) -> Generator[str, None, None]:
    """Stream one assistant turn as SSE events with fallback-to-full-reply logic."""
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
        for chunk in get_model_stream(messages, params):
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
                fallback_reply = get_model_reply(messages, params)
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
    updated = prompt_and_reply(messages, user_input, params=params)
    save_messages_to_firestore(conversation_id, updated, metadata=metadata)
    return updated
