"""Read, anonymize, and sample complete archives without exposing archive metadata.

Historical imports occupy a separate private GCS prefix. The sampler caches parsed
objects per worker and checks object generations every five minutes; it never
uses the review manifest or modifies administrative transcripts.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import logging
from pathlib import Path
import random
import re
import threading
import time
from typing import Any

HISTORICAL_PREFIX = "sampled_sessions/historical/"
ARCHIVE_PREFIXES = ("zbchats/", HISTORICAL_PREFIX)
CACHE_SECONDS = 300
SAMPLE_COUNT = 5
SPEAKERS = {"system": "System", "user": "Student", "assistant": "Mumonbot"}
# These identities belong to the teacher or to the quoted historical cases.
_NON_STUDENT_NAMES = {
    "student",
    "user",
    "guest",
    "anon",
    "anonymous",
    "unknown",
    "unk",
    "local",
    "web",
    "api",
    "api-caller",
    "system",
    "assistant",
    "zenbot",
    "mumonbot",
    "mumon",
    "ekai",
    "joshu",
    "nansen",
    "ummon",
    "tokusan",
    "rinzai",
    "hakuin",
    "tenkei",
    "setcho",
    "buddha",
    "bodhidharma",
}
_SELF_NAME = re.compile(
    r"\b(?i:my name is|call me)\s+([A-Z][a-z]+(?:[ '-][A-Z][a-z]+){0,2})\b"
)
_RECORDING_DATE = re.compile(r"(?<!\d)((?:19|20)\d{6})(?:-(\d{6})(?!\d))?")


class ArchiveUnavailable(RuntimeError):
    """The private archive storage could not be read."""


@dataclass(frozen=True)
class Message:
    """A complete message, with text segments kept in their recorded order."""

    role: str
    parts: tuple[str, ...]


@dataclass(frozen=True)
class Recording:
    """Internal parsed archive; identity fields never reach the template."""

    metadata: dict[str, Any]
    messages: tuple[Message, ...]
    fingerprint: str
    source_name: str


def parse_recording(payload: bytes | str, source_name: str) -> Recording:
    """Parse an entire JSONL transcript, rejecting any unsupported message."""
    text = (
        payload.decode("utf-8-sig")
        if isinstance(payload, bytes)
        else payload.lstrip("\ufeff")
    )
    metadata: dict[str, Any] = {}
    messages = []
    for line in text.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError("Archive row must be an object")
        if "role" not in row:
            if messages:
                raise ValueError("Metadata found inside dialogue")
            metadata.update(row)
            continue
        role = row["role"]
        if role not in SPEAKERS:
            raise ValueError("Unsupported speaker")
        content = row.get("content")
        if isinstance(content, str):
            parts = (content,)
        elif isinstance(content, list) and content:
            parts_list = []
            for part in content:
                if isinstance(part, str):
                    parts_list.append(part)
                elif (
                    isinstance(part, dict)
                    and part.get("type") == "text"
                    and isinstance(part.get("text"), str)
                ):
                    parts_list.append(part["text"])
                else:
                    raise ValueError("Unsupported message content")
            parts = tuple(parts_list)
        else:
            raise ValueError("Unsupported message content")
        messages.append(Message(role, parts))
    if not messages:
        raise ValueError("Archive contains no dialogue")
    canonical = [(m.role, m.parts) for m in messages]
    fingerprint = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return Recording(metadata, tuple(messages), fingerprint, source_name)


def _student_aliases(recording: Recording) -> set[str]:
    """Find recorded student identities, without interpreting ordinary dialogue."""
    names = set()
    student = recording.metadata.get("student")
    if isinstance(student, str) and student.strip():
        names.add(student.strip())
    identifier = str(
        recording.metadata.get("conversation_id")
        or recording.metadata.get("c_id")
        or Path(recording.source_name).stem
    )
    # Older IDs embed the student before the case number and recording date.
    alias = re.split(r"-(?:mmnk\d+|(?:19|20)\d{6})(?:-|$)", identifier, maxsplit=1)[0]
    alias = re.sub(r"^(?:zbchat|zb|zenbot)-", "", alias, flags=re.IGNORECASE)
    if alias != identifier and re.match(
        r"^(?:zbchat|zb|zenbot)-", identifier, re.IGNORECASE
    ):
        names.add(alias)
    for message in recording.messages:
        if message.role == "user":
            for part in message.parts:
                names.update(match.group(1) for match in _SELF_NAME.finditer(part))
    # A recorded full name also identifies its first-name form in replies.
    names.update(
        name.split()[0]
        for name in tuple(names)
        if re.fullmatch(r"[A-Z][a-z]+(?: [A-Z][a-z]+)+", name)
    )
    return {
        name
        for name in names
        if name.casefold() not in _NON_STUDENT_NAMES and len(name) > 1
    }


def _recorded_date(recording: Recording) -> str:
    """Resolve a validated date/time without guessing a historical timezone."""

    def timestamp(value: Any) -> str | None:
        if not isinstance(value, str) or not re.match(
            r"^\d{4}-\d{2}-\d{2}(?:$|[T ])", value
        ):
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if len(value) == 10:
            return parsed.date().isoformat()
        display = parsed.isoformat(sep=" ", timespec="seconds")
        return display[:-6] + " UTC" if display.endswith("+00:00") else display

    created = timestamp(recording.metadata.get("created_at"))
    if created:
        return created
    for identifier in (
        Path(recording.source_name).stem,
        recording.metadata.get("conversation_id"),
        recording.metadata.get("c_id"),
    ):
        if not isinstance(identifier, str):
            continue
        match = _RECORDING_DATE.search(identifier)
        if match:
            try:
                date = datetime.strptime(match[1], "%Y%m%d")
            except ValueError:
                continue
            if match[2]:
                try:
                    return datetime.strptime(
                        match[1] + match[2], "%Y%m%d%H%M%S"
                    ).isoformat(sep=" ")
                except ValueError:
                    pass
            return date.date().isoformat()
    return timestamp(recording.metadata.get("saved_at")) or "Date unavailable"


def public_recording(recording: Recording) -> dict[str, Any]:
    """Return only anonymized text, model, and date for public rendering."""
    aliases = _student_aliases(recording)
    pattern = (
        re.compile(
            r"(?<!\w)(?:"
            + "|".join(re.escape(n) for n in sorted(aliases, key=len, reverse=True))
            + r")(?!\w)",
            re.IGNORECASE,
        )
        if aliases
        else None
    )

    def redact(text: str) -> str:
        return pattern.sub("Student", text) if pattern else text

    model = recording.metadata.get("model") or recording.metadata.get("model_name")
    return {
        "model": (
            redact(model) if isinstance(model, str) and model else "Model unavailable"
        ),
        "date": _recorded_date(recording),
        "messages": [
            {"speaker": SPEAKERS[m.role], "parts": tuple(redact(p) for p in m.parts)}
            for m in recording.messages
        ],
    }


class ArchiveSampler:
    """Cache GCS object generations and parsed recordings for one app worker."""

    def __init__(self, ttl: float = CACHE_SECONDS):
        """Create an empty cache with a monotonic refresh interval."""
        self.ttl = ttl
        self._lock = threading.Lock()
        self._buckets: tuple[Any, Any] = (None, None)
        self._expires = 0.0
        self._objects: dict[tuple[int, str], tuple[Any, Recording | None]] = {}
        self._pool: tuple[Recording, ...] = ()

    def sample(
        self, bucket: Any, count: int = SAMPLE_COUNT, historical_bucket: Any = None
    ) -> list[dict[str, Any]]:
        """Refresh changed archives as needed and make a fresh uniform draw."""
        if bucket is None:
            raise ArchiveUnavailable("Archive storage unavailable")
        with self._lock:
            same_buckets = (
                bucket is self._buckets[0] and historical_bucket is self._buckets[1]
            )
            if not same_buckets or time.monotonic() >= self._expires:
                objects = {}
                old = self._objects if same_buckets else {}
                try:
                    pending = []
                    sources = [(bucket, "zbchats/")]
                    if historical_bucket is not None:
                        sources.append((historical_bucket, HISTORICAL_PREFIX))
                    for source_index, (source_bucket, prefix) in enumerate(sources):
                        for blob in source_bucket.list_blobs(prefix=prefix):
                            if not blob.name.endswith(".jsonl"):
                                continue
                            key = (source_index, blob.name)
                            version = (
                                getattr(blob, "generation", None),
                                getattr(blob, "metageneration", None),
                                getattr(blob, "updated", None),
                            )
                            if (
                                key in old
                                and old[key][0] == version
                                and any(v is not None for v in version)
                            ):
                                objects[key] = old[key]
                                continue
                            pending.append((key, version, blob))
                    # A cold worker may have years of small archives to load.
                    # Bound parallel reads to avoid serial network round trips.
                    with ThreadPoolExecutor(max_workers=8) as executor:
                        loaded = executor.map(
                            _read_recording, (item[2] for item in pending)
                        )
                        for (key, version, _), recording in zip(pending, loaded):
                            objects[key] = (version, recording)
                except Exception as exc:
                    # Do not serve a stale pool after a failed refresh: it could
                    # contain a recording removed by the operator.
                    raise ArchiveUnavailable("Archive refresh failed") from exc
                unique = {}
                # Prefer current cloud records when a historical copy exists.
                for key in sorted(objects):
                    _, recording = objects[key]
                    if recording is not None:
                        unique.setdefault(recording.fingerprint, recording)
                self._buckets = (bucket, historical_bucket)
                self._objects = objects
                self._pool = tuple(unique.values())
                self._expires = time.monotonic() + self.ttl
            selected = random.sample(self._pool, min(count, len(self._pool)))
        return [public_recording(recording) for recording in selected]


sampler = ArchiveSampler()


def _read_recording(blob: Any) -> Recording | None:
    """Download a whole object; skip malformed text but propagate storage failures."""
    payload = blob.download_as_bytes()
    try:
        # Imports retain original filenames in private object metadata for dates.
        name = (getattr(blob, "metadata", None) or {}).get(
            "recording_name"
        ) or blob.name
        return parse_recording(payload, name)
    except (ValueError, UnicodeError, TypeError):
        logging.warning("Skipping unreadable sampled archive: %s", blob.name)
        return None


def import_historical(
    bucket: Any, directories: list[Path], apply: bool = False
) -> dict[str, int]:
    """Report or import unique historical archives without replacing existing objects.

    The private destination uses transcript hashes as filenames. The original
    filename is retained in GCS object metadata for legacy recording dates.
    Conditional creation makes repeat imports safe, including concurrent runs.
    """
    report = {
        "scanned": 0,
        "invalid": 0,
        "duplicates": 0,
        "existing": 0,
        "pending": 0,
        "uploaded": 0,
    }
    records: dict[str, tuple[Path, bytes]] = {}
    for directory in directories:
        for path in sorted(directory.glob("*.jsonl")):
            report["scanned"] += 1
            try:
                payload = path.read_bytes()
                recording = parse_recording(payload, path.name)
            except (ValueError, UnicodeError, TypeError):
                report["invalid"] += 1
                logging.warning("Skipping unreadable historical archive: %s", path)
                continue
            if recording.fingerprint in records:
                report["duplicates"] += 1
            else:
                records[recording.fingerprint] = (path, payload)
    for fingerprint, (path, payload) in records.items():
        blob = bucket.blob(f"{HISTORICAL_PREFIX}{fingerprint}.jsonl")
        if blob.exists():
            report["existing"] += 1
            continue
        report["pending"] += 1
        if apply:
            blob.metadata = {"recording_name": path.name}
            try:
                blob.upload_from_string(
                    payload, content_type="application/jsonl", if_generation_match=0
                )
            except Exception:
                # Another importer may have created the same hash meanwhile.
                # Conditional creation still prevents an overwrite.
                if not blob.exists():
                    raise
                report["pending"] -= 1
                report["existing"] += 1
                continue
            report["uploaded"] += 1
    return report
