"""Intentionally refresh the deployed MCP output-contract snapshot from Actions."""

import argparse
import json
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]


def build_snapshot(source: dict) -> dict:
    """Collect only output schemas and their transitive references."""
    operations = {
        op["operationId"]: op["responses"]["200"]["content"]["application/json"][
            "schema"
        ]
        for methods in source["paths"].values()
        for op in methods.values()
        if "operationId" in op
    }
    operations["replaceMemoryLogbook"] = operations["commitMemoryEntry"]
    operations["getOperationStatus"] = {
        "type": "object",
        "required": ["status", "operation_id", "state", "tool", "started_at"],
        "properties": {
            "status": {"type": "string"},
            "operation_id": {"type": "string"},
            "state": {"enum": ["running", "completed", "uncertain"]},
            "tool": {"type": "string"},
            "started_at": {"type": "string"},
            "result": {"type": "object"},
        },
    }
    used = {}

    def collect(value):
        if isinstance(value, dict):
            if "$ref" in value:
                key = value["$ref"].rsplit("/", 1)[-1]
                if key not in used:
                    used[key] = source["components"]["schemas"][key]
                    collect(used[key])
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(operations)
    return {
        "source_version": source["info"]["version"],
        "operations": operations,
        "schemas": used,
    }


def main():
    """Write the snapshot only when explicitly invoked by its maintainer."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=APP_ROOT.parent / "zenbot_knowledge" / "action_schemas.json",
    )
    args = parser.parse_args()
    source = json.loads(args.source.read_text(encoding="utf-8"))
    target = APP_ROOT / "zb_mcp" / "output_schemas.json"
    target.write_text(
        json.dumps(build_snapshot(source), indent=2) + "\n", encoding="utf-8"
    )
    print(f"Updated {target}")


if __name__ == "__main__":
    main()
