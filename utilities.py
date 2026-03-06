# utilities.py
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
import random as rnd
import time
import uuid
from pathlib import Path
from typing import Any, Generator, Optional

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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _choose_session_profile() -> dict[str, Any]:
    model_key = rnd.choice(list(config.MODELS.keys()))
    profile = rnd.choice(list(config.MODEL_ARGS.keys()))
    params = config.make_params(profile, model_name=model_key)
    return {
        "model_name": model_key,
        "profile": profile,
        "training_loss": MODEL_LOSSES.get(model_key, 0.0),
        "params": params,
    }


def _build_metadata(
    student: str,
    case_id: str | None = None,
    session_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
    if not conversation_id:
        return config.START_CHATS["smiles"].copy(), {}

    if DB is None:
        payload = _LOCAL_CONVERSATIONS.get(conversation_id)
        if not payload:
            return config.START_CHATS["smiles"].copy(), {}
        return payload.get("messages", []), payload.get("metadata", {})

    try:
        doc = DB.collection("conversations").document(conversation_id).get()
    except Exception as e:
        logging.error("Error retrieving conversation %s: %s", conversation_id, e)
        return config.START_CHATS["smiles"].copy(), {}

    if not doc.exists:
        return config.START_CHATS["smiles"].copy(), {}

    payload = doc.to_dict() or {}
    return payload.get("messages", []), payload.get("metadata", {})


def get_messages_from_firestore(conversation_id: str) -> list[dict[str, str]]:
    messages, _meta = get_conversation_state(conversation_id)
    return messages


def get_conversation_metadata(conversation_id: str) -> dict[str, Any]:
    _messages, metadata = get_conversation_state(conversation_id)
    return metadata


def delete_messages_from_firestore(conversation_id: str) -> None:
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
    # Retained for backward compatibility; state is now conversation-scoped.
    return _build_metadata(student=student or "unk", case_id=case_id)


# ---------------- Koan Tools ----------------


def _load_mmnk_cases() -> list[dict[str, Any]]:
    with open("static/mmnk.json", "r", encoding="utf-8") as f:
        payload = json.load(f)
    cases = payload.get("cases", [])
    return cases if isinstance(cases, list) else []


def get_mmnk_case(case_id: str) -> Optional[dict[str, Any]]:
    try:
        cases = _load_mmnk_cases()
        return next((k for k in cases if str(k.get("id")) == str(case_id)), None)
    except Exception as e:
        logging.error("Error retrieving koan %s: %s", case_id, e)
        return None


def get_random_koan_case_id() -> str:
    try:
        cases = _load_mmnk_cases()
        if cases:
            return str(rnd.choice(cases).get("id"))
    except Exception as e:
        logging.error("Error retrieving random koan case ID: %s", e)
    return str(rnd.choice(range(1, 49)))


def get_mmnk_text(case_id: str) -> str:
    koan = get_mmnk_case(case_id)
    if not koan:
        return ""
    body = koan.get("body")
    return body if isinstance(body, str) else ""


def koan_startup(koan: dict[str, Any]) -> list[dict[str, str]]:
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
    conversation_id, _messages, _meta = create_conversation(
        student=student, case_id=case_id
    )
    return conversation_id


def get_or_init_params(metadata: dict[str, Any]) -> dict[str, Any]:
    params = metadata.get("params") if isinstance(metadata, dict) else None
    if isinstance(params, dict) and params.get("model"):
        return params
    fallback = _choose_session_profile()
    return fallback["params"]


# ---------------- Model Interaction ----------------


def _get_openai_client():
    if BOTLING is None or openai is None:
        raise ModelAPIError(
            "OpenAI client unavailable. Install `openai` and set OPENAI_API_KEY."
        )
    return BOTLING


def get_model_stream(
    messages: list[dict[str, str]],
    params: dict[str, Any],
) -> Generator[str, None, None]:
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
    client = _get_openai_client()
    max_attempts = 2
    for attempt in range(1, max_attempts + 1):
        try:
            completion = client.chat.completions.create(messages=messages, **params)
            return completion.choices[0].message.content
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


def prompt_and_reply(
    messages: list[dict[str, str]],
    prompt: str,
    params: dict[str, Any],
) -> list[dict[str, str]]:
    messages.append({"role": "user", "content": prompt})
    reply = get_model_reply(messages, params)
    messages.append({"role": "assistant", "content": reply})
    return messages


def _sse(payload: dict[str, Any]) -> str:
    return "data: " + json.dumps(payload) + "\n\n"


def friendly_model_error_message(raw_error: str) -> str:
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
    lines = [json.dumps(params)] + [json.dumps(m) for m in messages]
    return "\n".join(lines)


def save_chat_to_file(
    data: list[dict[str, Any]], params: dict[str, Any], file_path: str
) -> None:
    content = to_jsonl(params, data)
    Path(file_path).parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)


def get_chat_from_local_file(file_path: str) -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            data.append(json.loads(line.strip()))
    return data


def save_chat_to_bucket(
    data: list[dict[str, Any]], params: dict[str, Any], blob_name: str
) -> None:
    if not BUCKET:
        raise RuntimeError("GCS bucket client unavailable; cannot save chat to bucket.")
    blob = BUCKET.blob(blob_name)
    blob.upload_from_string(to_jsonl(params, data), content_type="application/jsonl")


def get_all_conversations_from_gcs() -> dict[str, list[dict[str, Any]]]:
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


def download_all() -> bool:
    try:
        if not config.LOCAL:
            logging.warning("download_all skipped outside local environment")
            return False

        full_bucket = get_all_conversations_from_gcs()
        for c_id, c_text in full_bucket.items():
            filename = c_id.replace('"', "") + ".jsonl"
            filepath = os.path.join("config", "zbchats", filename)
            lines = [json.dumps(item) for item in c_text]
            with open(filepath, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        return True
    except Exception as e:
        logging.error("download_all failed: %s", e)
        return False


def list_conversation_files_in_gcs() -> list[str]:
    if not BUCKET:
        return []
    return [
        b.name
        for b in BUCKET.list_blobs(prefix="zbchats/")
        if b.name.endswith(".jsonl")
    ]


def get_conversation_from_gcs(conversation_id: str) -> list[dict[str, Any]] | None:
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


def load_memory_logbook() -> list[dict[str, Any]]:
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

    if payload is None:
        try:
            if _LOCAL_LOGBOOK_PATH.exists():
                payload = _LOCAL_LOGBOOK_PATH.read_text(encoding="utf-8")
        except Exception as e:
            logging.warning("Error loading local memory logbook: %s", e)

    return _parse_logbook_payload(payload or "")


def save_logbook(logbook: list[dict[str, Any]]) -> None:
    lines = [json.dumps(item, ensure_ascii=False, default=str) for item in logbook]
    payload = "\n".join(lines)

    if BUCKET:
        blob = BUCKET.blob(MEMORY_LOGBOOK)
        blob.upload_from_string(payload, content_type="application/jsonl")
    else:
        _LOCAL_LOGBOOK_PATH.parent.mkdir(parents=True, exist_ok=True)
        _LOCAL_LOGBOOK_PATH.write_text(payload, encoding="utf-8")


def update_logbook(new_entry: dict[str, Any]) -> list[dict[str, Any]]:
    logbook = load_memory_logbook()
    logbook.append(new_entry)
    save_logbook(logbook)
    return logbook


# -------- Request Helpers --------
def get_request_data() -> dict[str, Any]:
    if request.method == "POST":
        return request.get_json(silent=True) or {}
    return request.args.to_dict()


def process_chat(conversation_id: str, user_input: str) -> list[dict[str, str]]:
    messages, metadata = get_conversation_state(conversation_id)
    params = get_or_init_params(metadata)
    updated = prompt_and_reply(messages, user_input, params=params)
    save_messages_to_firestore(conversation_id, updated, metadata=metadata)
    return updated
