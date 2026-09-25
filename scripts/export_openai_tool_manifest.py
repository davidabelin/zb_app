"""Export the internal Zenbot Responses tool catalog to a JSON manifest.

This is a runtime diagnostics helper. GPT-facing ChatGPT Actions belong in
`zenbot_knowledge/action_schemas.yaml` and its generated JSON copy, not in this
internal Responses function-tool manifest.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys


def _output_path() -> Path:
    """Return the app-local default output path for diagnostic exports."""

    return Path(__file__).resolve().parents[1] / "generated" / "openai_response_tools.json"


def _load_utilities_module():
    """Import `utilities` after ensuring `zb_app` is on `sys.path`."""

    app_root = Path(__file__).resolve().parents[1]
    if str(app_root) not in sys.path:
        sys.path.insert(0, str(app_root))

    import utilities

    return utilities


def main() -> int:
    """Write the current tool catalog to disk and print the destination path."""

    utilities = _load_utilities_module()
    target = _output_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            utilities.response_tool_definitions(include_web_search=True),
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
