"""Sync curated Zenbot runtime files into an OpenAI vector store.

This script is intentionally conservative. It uploads a small set of operator
and runtime reference files that are useful for File Search in the v3 runtime:

- koan source text (`static/mmnk.json`)
- runtime/operator docs
- the GPT action schema
- the active project todo and curation notes

It does not attempt to bulk-upload every archived conversation. That should be a
separate, more deliberate data-pipeline job once set05 curation is stable.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Iterable

from openai import OpenAI


def _repo_root() -> Path:
    """Return the repository root above `zb_app`."""

    return Path(__file__).resolve().parents[2]


def _load_config_class():
    """Import `Config` after ensuring `zb_app` is on `sys.path`."""

    app_root = Path(__file__).resolve().parents[1]
    if str(app_root) not in sys.path:
        sys.path.insert(0, str(app_root))

    from config import Config

    return Config


def _default_paths(repo_root: Path) -> list[Path]:
    """Return the default file set for vector-store sync."""

    return [
        repo_root / "zb_app" / "static" / "mmnk.json",
        repo_root / "zb_app" / "README.md",
        repo_root / "project" / "docs" / "ARCHITECTURE.md",
        repo_root / "project" / "docs" / "DEVELOPER_GUIDE.md",
        repo_root / "project" / "docs" / "UI_POLISH_PLAN_alpha.md",
        repo_root / "zenbot_knowledge" / "solutions.md",
        repo_root / "zenbot_knowledge" / "initialization.md",
        repo_root / "zenbot_knowledge" / "customization_instructions.md",
        repo_root / "zenbot_knowledge" / "action_schemas.yaml",
        repo_root / "zenbot_knowledge" / "openai_response_tools.json",
        repo_root / "project" / "rolling_to_do_list.md",
        repo_root / "training" / "trainset04" / "readme_set04.md",
        repo_root / "training" / "generated" / "trainset_ready_v2" / "report.json",
    ]


def _existing_paths(paths: Iterable[Path]) -> list[Path]:
    """Filter a candidate path sequence down to existing files only."""

    return [path.resolve() for path in paths if path.exists() and path.is_file()]


def _ensure_vector_store(
    client: OpenAI,
    vector_store_id: str,
    create_if_missing: bool,
    name: str,
) -> str:
    """Return a vector store ID, optionally creating one first."""

    if vector_store_id:
        return vector_store_id
    if not create_if_missing:
        raise ValueError(
            "No vector store ID provided. Pass --vector-store-id or --create."
        )

    vector_store = client.vector_stores.create(
        name=name,
        description="Zenbot v3 runtime references and koan source documents",
    )
    return vector_store.id


def _upload_file(client: OpenAI, vector_store_id: str, path: Path) -> dict[str, str]:
    """Upload one file and attach it to the requested vector store."""

    with path.open("rb") as handle:
        uploaded = client.files.create(file=handle, purpose="assistants")

    client.vector_stores.files.create(
        vector_store_id,
        file_id=uploaded.id,
        attributes={
            "path": path.as_posix(),
            "filename": path.name,
        },
    )
    return {
        "path": str(path),
        "file_id": uploaded.id,
        "vector_store_id": vector_store_id,
    }


def main() -> int:
    """Parse arguments, upload files, and print a JSON summary."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vector-store-id", default="", help="Existing vector store ID.")
    parser.add_argument(
        "--create",
        action="store_true",
        help="Create a new vector store when none is supplied.",
    )
    parser.add_argument(
        "--name",
        default="zenbot-v3-runtime",
        help="Name for a newly created vector store.",
    )
    parser.add_argument(
        "--path",
        action="append",
        default=[],
        help="Additional file path to upload. Can be used multiple times.",
    )
    args = parser.parse_args()

    Config = _load_config_class()
    config = Config()
    if not config.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is required.")

    repo_root = _repo_root()
    paths = _default_paths(repo_root)
    paths.extend(repo_root / raw_path for raw_path in args.path)
    upload_paths = _existing_paths(paths)
    if not upload_paths:
        raise RuntimeError("No existing files found to upload.")

    default_vector_store_id = config.OPENAI_VECTOR_STORE_IDS[0] if config.OPENAI_VECTOR_STORE_IDS else ""
    client = OpenAI(api_key=config.OPENAI_API_KEY)
    vector_store_id = _ensure_vector_store(
        client,
        args.vector_store_id or default_vector_store_id,
        create_if_missing=args.create,
        name=args.name,
    )

    uploaded = [_upload_file(client, vector_store_id, path) for path in upload_paths]
    print(
        json.dumps(
            {
                "vector_store_id": vector_store_id,
                "uploaded_count": len(uploaded),
                "uploaded": uploaded,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
