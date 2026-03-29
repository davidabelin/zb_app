"""Export the Zenbot Responses tool catalog to a JSON manifest."""

from __future__ import annotations

import json
from pathlib import Path
import sys


def _output_path() -> Path:
    """Return the default output path in `zenbot_knowledge`."""

    return Path(__file__).resolve().parents[2] / "zenbot_knowledge" / "openai_response_tools.json"


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
