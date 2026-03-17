"""Local admin helpers for deduplicating sessions and rebuilding ``review.csv``."""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


ROLE_SET = {"system", "user", "assistant"}
SOURCE_SUBDIRS = ("local", "web", "use")
SOURCE_PRIORITY = {"local": 3, "web": 2, "use": 1}
REVIEW_FIELDNAMES = [
    "id",
    "keep",
    "conversation_id",
    "source_path",
    "model",
    "student",
    "num_messages",
    "preview_user",
    "preview_assistant",
    "bad_json_lines",
]
REVIEW_EVALUATIONS = {"Use", "Alter", "Reject"}


@dataclass(frozen=True)
class SessionRecord:
    """One parsed session candidate for the trainset04 review queue."""

    path: Path
    source_path: str
    source_subdir: str
    conversation_id: str
    model: str
    student: str
    messages: list[dict[str, str]]
    bad_json_lines: int
    transcript_hash: str

    @property
    def record_id(self) -> str:
        """Return a stable review row identifier derived from the transcript."""
        return self.transcript_hash[:16]


def _is_message_obj(obj: object) -> bool:
    if not isinstance(obj, dict):
        return False
    return obj.get("role") in ROLE_SET and isinstance(obj.get("content"), str)


def _truncate_one_line(text: str, limit: int) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)] + "..."


def _pick_preview(messages: list[dict[str, str]]) -> tuple[str, str]:
    user = next(
        (
            message["content"]
            for message in messages
            if message["role"] == "user" and message["content"].strip()
        ),
        "",
    )
    assistant = next(
        (
            message["content"]
            for message in messages
            if message["role"] == "assistant" and message["content"].strip()
        ),
        "",
    )
    return _truncate_one_line(user, 180), _truncate_one_line(assistant, 180)


def _transcript_hash(messages: list[dict[str, str]]) -> str:
    payload = json.dumps(messages, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_session_file(path: Path, repo_root: Path) -> SessionRecord | None:
    """Parse one archived JSONL session file into review-ready metadata."""
    metadata: dict[str, Any] = {}
    messages: list[dict[str, str]] = []
    bad_json_lines = 0

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            bad_json_lines += 1
            continue

        if not messages and not metadata and isinstance(obj, dict) and not _is_message_obj(obj):
            metadata = obj
            continue
        if _is_message_obj(obj):
            messages.append({"role": obj["role"], "content": obj["content"]})

    if not messages:
        return None

    conversation_id = str(
        metadata.get("conversation_id") or metadata.get("id") or path.stem
    ).strip() or path.stem
    source_subdir = path.parent.name
    return SessionRecord(
        path=path,
        source_path=path.relative_to(repo_root).as_posix(),
        source_subdir=source_subdir,
        conversation_id=conversation_id,
        model=str(metadata.get("model") or metadata.get("model_name") or "").strip(),
        student=str(metadata.get("student") or "").strip(),
        messages=messages,
        bad_json_lines=bad_json_lines,
        transcript_hash=_transcript_hash(messages),
    )


def _load_existing_keep_by_hash(
    review_csv_path: Path, repo_root: Path
) -> dict[str, str]:
    decisions: dict[str, str] = {}
    if not review_csv_path.exists():
        return decisions

    with review_csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            keep_value = str(row.get("keep") or "").strip()
            source_path = str(row.get("source_path") or "").strip()
            if not keep_value or not source_path:
                continue

            candidate = (repo_root / Path(source_path)).resolve()
            if not candidate.exists():
                continue
            record = parse_session_file(candidate, repo_root)
            if record and record.transcript_hash not in decisions:
                decisions[record.transcript_hash] = keep_value

    return decisions


def _canonical_rank(record: SessionRecord) -> tuple[int, int, int, int, str]:
    return (
        0 if record.path.stem == record.conversation_id else 1,
        -SOURCE_PRIORITY.get(record.source_subdir, 0),
        -len(record.path.stem),
        -len(record.messages),
        record.source_path,
    )


def _choose_canonical(records: list[SessionRecord]) -> SessionRecord:
    return min(records, key=_canonical_rank)


def _target_duplicate_path(duplicates_root: Path, record: SessionRecord) -> Path:
    target = duplicates_root / record.source_subdir / record.path.name
    if not target.exists():
        return target

    stem = record.path.stem
    suffix = record.path.suffix
    counter = 1
    while True:
        candidate = target.with_name(f"{stem}__dupe{counter}{suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def _quarantine_duplicates(
    duplicates: list[SessionRecord], duplicates_root: Path
) -> int:
    moved = 0
    for record in duplicates:
        target = _target_duplicate_path(duplicates_root, record)
        target.parent.mkdir(parents=True, exist_ok=True)
        record.path.replace(target)
        moved += 1
    return moved


def _write_review_csv(
    review_csv_path: Path,
    records: list[SessionRecord],
    keep_by_hash: dict[str, str],
) -> None:
    review_csv_path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="",
        delete=False,
        dir=review_csv_path.parent,
    ) as handle:
        temp_path = Path(handle.name)
        writer = csv.DictWriter(handle, fieldnames=REVIEW_FIELDNAMES)
        writer.writeheader()
        for record in records:
            preview_user, preview_assistant = _pick_preview(record.messages)
            writer.writerow(
                {
                    "id": record.record_id,
                    "keep": keep_by_hash.get(record.transcript_hash, ""),
                    "conversation_id": record.conversation_id,
                    "source_path": record.source_path,
                    "model": record.model,
                    "student": record.student,
                    "num_messages": len(record.messages),
                    "preview_user": preview_user,
                    "preview_assistant": preview_assistant,
                    "bad_json_lines": record.bad_json_lines,
                }
            )
    temp_path.replace(review_csv_path)


def _normalize_evaluation(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered == "use":
        return "Use"
    if lowered == "alter":
        return "Alter"
    if lowered == "reject":
        return "Reject"
    return ""


def _write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        delete=False,
        dir=path.parent,
    ) as handle:
        temp_path = Path(handle.name)
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    temp_path.replace(path)


def _load_existing_evaluations_by_hash(reviewed_jsonl_path: Path) -> dict[str, str]:
    evaluations: dict[str, str] = {}
    if not reviewed_jsonl_path.exists():
        return evaluations

    for raw_line in reviewed_jsonl_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        messages = row.get("messages")
        if not isinstance(messages, list):
            continue

        cleaned_messages: list[dict[str, str]] = []
        for message in messages:
            if not _is_message_obj(message):
                cleaned_messages = []
                break
            cleaned_messages.append(
                {"role": message["role"], "content": message["content"]}
            )
        if not cleaned_messages:
            continue

        evaluation = _normalize_evaluation(row.get("evaluation"))
        if not evaluation:
            continue
        transcript_hash = _transcript_hash(cleaned_messages)
        if transcript_hash not in evaluations:
            evaluations[transcript_hash] = evaluation

    return evaluations


def _sync_generated_review_files(
    repo_root: Path, canonicals: list[SessionRecord]
) -> dict[str, int | str]:
    generated_dir = (repo_root / "training" / "generated").resolve()
    messages_path = generated_dir / "collected_sessions_messages_only.jsonl"
    metadata_path = generated_dir / "collected_sessions_with_metadata.jsonl"
    reviewed_path = generated_dir / "collected_sessions_with_evaluations.jsonl"
    train_path = generated_dir / "sessions_to_train.jsonl"

    evaluations_by_hash = _load_existing_evaluations_by_hash(reviewed_path)

    messages_rows: list[dict[str, Any]] = []
    metadata_rows: list[dict[str, Any]] = []
    reviewed_rows: list[dict[str, Any]] = []
    train_rows: list[dict[str, Any]] = []

    evaluations_preserved = 0
    for record in canonicals:
        metadata = {
            "conversation_id": record.conversation_id,
            "source_path": record.source_path,
            "source_subdir": record.source_subdir,
        }
        if record.model:
            metadata["model"] = record.model
        if record.student:
            metadata["student"] = record.student

        messages_row = {"messages": record.messages}
        metadata_row = {"messages": record.messages, "metadata": metadata}
        evaluation = evaluations_by_hash.get(record.transcript_hash, "")
        reviewed_row = {
            "messages": record.messages,
            "metadata": metadata,
            "evaluation": evaluation,
        }

        messages_rows.append(messages_row)
        metadata_rows.append(metadata_row)
        reviewed_rows.append(reviewed_row)
        if evaluation:
            evaluations_preserved += 1
        if evaluation == "Use":
            train_rows.append({"messages": record.messages})

    _write_jsonl_atomic(messages_path, messages_rows)
    _write_jsonl_atomic(metadata_path, metadata_rows)
    _write_jsonl_atomic(reviewed_path, reviewed_rows)
    _write_jsonl_atomic(train_path, train_rows)

    summary: dict[str, int | str] = {
        "generated_rows": len(metadata_rows),
        "evaluations_preserved": evaluations_preserved,
        "train_rows": len(train_rows),
        "messages_jsonl": str(messages_path),
        "metadata_jsonl": str(metadata_path),
        "reviewed_jsonl": str(reviewed_path),
        "train_jsonl": str(train_path),
    }
    return summary


def sync_review_queue(
    repo_root: Path,
    review_csv_path: Path | None = None,
    quarantine_duplicates: bool = True,
) -> dict[str, int | str]:
    """Deduplicate collected sessions and rebuild ``training/trainset04/review.csv``."""
    root = repo_root.resolve()
    review_path = (
        review_csv_path.resolve()
        if review_csv_path is not None
        else (root / "training" / "trainset04" / "review.csv").resolve()
    )
    duplicates_root = (root / "collected_sessions" / "_duplicates").resolve()
    keep_by_hash = _load_existing_keep_by_hash(review_path, root)

    candidates: list[SessionRecord] = []
    for subdir in SOURCE_SUBDIRS:
        source_dir = root / "collected_sessions" / subdir
        if not source_dir.exists():
            continue
        for path in sorted(source_dir.glob("*.jsonl")):
            record = parse_session_file(path, root)
            if record is not None:
                candidates.append(record)

    grouped: dict[str, list[SessionRecord]] = {}
    for record in candidates:
        grouped.setdefault(record.transcript_hash, []).append(record)

    canonicals: list[SessionRecord] = []
    duplicates: list[SessionRecord] = []
    for group in grouped.values():
        winner = _choose_canonical(group)
        canonicals.append(winner)
        duplicates.extend(record for record in group if record.path != winner.path)

    if quarantine_duplicates and duplicates:
        _quarantine_duplicates(duplicates, duplicates_root)

    canonicals.sort(key=lambda record: record.source_path)
    _write_review_csv(review_path, canonicals, keep_by_hash)
    generated_summary = _sync_generated_review_files(root, canonicals)

    preserved = sum(
        1 for record in canonicals if keep_by_hash.get(record.transcript_hash, "").strip()
    )
    summary: dict[str, int | str] = {
        "records_scanned": len(candidates),
        "records_kept": len(canonicals),
        "duplicate_groups": sum(1 for group in grouped.values() if len(group) > 1),
        "duplicates_moved": len(duplicates) if quarantine_duplicates else 0,
        "decisions_preserved": preserved,
        "review_csv": str(review_path),
        "duplicates_dir": str(duplicates_root),
    }
    summary.update(generated_summary)
    return summary
