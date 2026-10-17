"""Canonical memory reads, candidates, and writes shared by REST and MCP."""

from typing import Any

import utilities as utipy
from contracts import MemoryEntry
from .errors import ServiceError


def summaries(limit: int = 12, end_index: int | None = None) -> dict[str, Any]:
    """Return newest-first compact summaries and their pagination metadata."""
    return {
        **utipy.load_memory_logbook_summary_page(limit=limit, end_index=end_index),
        "limit": limit,
    }


def get_entry(serial_number: str) -> dict[str, Any]:
    """Retrieve one full canonical memory."""
    memory = utipy.get_memory_logbook_entry(serial_number)
    if not memory:
        raise ServiceError(
            "memory_entry_not_found",
            "Memory entry not found.",
            404,
            serial_number=str(serial_number),
        )
    return {"serial_number": str(serial_number), "memory": memory}


def candidate(entry: MemoryEntry) -> dict[str, Any]:
    """Queue a candidate without updating the canonical logbook."""
    payload = {"queued_at": utipy._utc_now(), "entry": entry.model_dump(mode="json")}
    utipy._write_jsonl_record(
        utipy.config.MEMORY_CANDIDATE_QUEUE, payload, utipy._LOCAL_MEMORY_CANDIDATE_PATH
    )
    return {
        "message": "memory candidate queued for review; canonical memory logbook was not updated",
        "queued_at": payload["queued_at"],
        "serial_number": entry.serial_number,
        "title": entry.title,
        "canonical_logbook_updated": False,
        "queue_blob": utipy.config.MEMORY_CANDIDATE_QUEUE,
    }


def commit(entry: dict[str, Any], legacy: bool = False) -> dict[str, Any]:
    """Append and normalize an entry using the existing canonical writer."""
    updated = utipy.update_logbook(entry)
    saved = updated[-1] if updated else utipy.normalize_memory_entry(entry)
    return {
        "message": (
            "entry appended via POST (legacy body)"
            if legacy
            else "entry appended via POST"
        ),
        "count": len(updated),
        "serial_number": str(saved.get("serial_number", "")).strip(),
        "title": str(saved.get("title", "")).strip(),
    }


def replace(full_logbook: list[dict[str, Any]]) -> dict[str, Any]:
    """Replace and resequence the full canonical logbook."""
    normalized = utipy.save_logbook(full_logbook)
    return {"message": "logbook replaced via POST", "count": len(normalized)}
