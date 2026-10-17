"""Read-only, authenticated-at-transport preparation for the Zenbot guide."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from . import ServiceError, koans, memories

BUNDLE_PATH = Path(__file__).resolve().parents[1] / "zb_mcp/data/startup_bundle.json"
REQUIRED_DOCUMENTS = (
    "customization_instructions.md",
    "initialization.md",
    "special_visitor_instructions.txt",
    "additional_instructions.md",
    "channelling_readme.md",
)


def _incomplete(message: str, **details: Any) -> ServiceError:
    return ServiceError("preparation_incomplete", message, 503, **details)


def load_bundle() -> dict[str, Any]:
    """Validate deployed resources without reading a sibling checkout or caching them."""
    try:
        bundle = json.loads(BUNDLE_PATH.read_text(encoding="utf-8"))
        if bundle["schema_version"] != 1 or not bundle["bundle_version"]:
            raise ValueError("Unsupported bundle version")
        documents = bundle["documents"]
        if [row["name"] for row in documents] != list(REQUIRED_DOCUMENTS):
            raise ValueError("Missing or reordered required documents")
        for row in documents:
            content = row["content"]
            if (
                not content.strip()
                or hashlib.sha256(content.encode()).hexdigest() != row["sha256"]
            ):
                raise ValueError("Empty document or content hash mismatch")
        policy = bundle["memory_policy"]
        if policy["recent_summary_limit"] != 12 or policy["newest_full_count"] != 2:
            raise ValueError("Unsupported memory policy")
        selected = policy["formative_memories"]
        if not selected or len({r["serial_number"] for r in selected}) != len(selected):
            raise ValueError("Invalid formative selection")
        if any(not r["serial_number"] or not r["expected_title"] for r in selected):
            raise ValueError("Missing formative identity")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise _incomplete(
            "The deployed startup document bundle is unavailable or invalid."
        ) from exc
    return bundle


def prepare() -> dict[str, Any]:
    """Return current documents and selected canonical memories; never write or infer."""
    bundle = load_bundle()
    policy = bundle["memory_policy"]
    page = memories.summaries(limit=policy["recent_summary_limit"])
    summaries = page["summaries"]
    if len(summaries) < policy["newest_full_count"]:
        raise _incomplete("The canonical logbook lacks the required newest entries.")
    requested = [r["serial_number"] for r in policy["formative_memories"]]
    requested.extend(
        r["serial_number"] for r in summaries[: policy["newest_full_count"]]
    )
    serials = list(dict.fromkeys(requested))
    expected = {
        r["serial_number"]: r["expected_title"] for r in policy["formative_memories"]
    }
    full_entries = []
    for serial in serials:
        try:
            entry = memories.get_entry(serial)["memory"]
        except ServiceError as exc:
            if exc.code != "memory_entry_not_found":
                raise
            raise _incomplete(
                "A required canonical memory is missing.", serial_number=serial
            ) from exc
        if entry["serial_number"] != serial or (
            serial in expected and entry["title"] != expected[serial]
        ):
            raise _incomplete(
                "A formative memory no longer matches its configured identity.",
                serial_number=serial,
            )
        full_entries.append(entry)
    # Detect append/resequence races rather than returning a knowingly mixed selection.
    if memories.summaries(limit=policy["recent_summary_limit"]) != page:
        raise _incomplete("The logbook changed during preparation; read it again.")
    koan = koans.random_koan()
    return {
        "preparation_schema_version": 1,
        "bundle_version": bundle["bundle_version"],
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "documents": bundle["documents"],
        "memory_summaries": summaries,
        "memories": full_entries,
        "latest_memory_serial": summaries[0]["serial_number"],
        "total_memory_count": page["total_count"],
        "loaded_document_names": [r["name"] for r in bundle["documents"]],
        "loaded_memory_serials": serials,
        "koan": koan,
    }
