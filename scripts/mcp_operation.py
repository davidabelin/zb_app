"""Inspect or explicitly reconcile a private MCP operation using operator ADC."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from google.cloud import firestore  # noqa: E402
from zb_mcp.operations import OperationStore  # noqa: E402


def main():
    """Require a reviewed result and stopped-worker acknowledgement before unlocking."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["status", "resolve"])
    parser.add_argument("--project", default="zenbot-434517")
    parser.add_argument(
        "--subject",
        required=True,
        help="Google sub from the operation record, not the email",
    )
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--result-file", type=Path)
    parser.add_argument("--note")
    parser.add_argument("--worker-stopped", action="store_true")
    args = parser.parse_args()
    if args.command == "resolve" and not (
        args.result_file and args.note and args.worker_stopped
    ):
        parser.error(
            "resolve requires --result-file, --note, and --worker-stopped after manual reconciliation"
        )
    store = OperationStore(firestore.Client(project=args.project))
    if args.command == "resolve":
        result = json.loads(args.result_file.read_text(encoding="utf-8"))
        if not isinstance(result, dict):
            parser.error("result file must contain one JSON object")
        store.reconcile(args.subject, args.operation_id, result, args.note)
    print(
        json.dumps(
            store.status(args.subject, args.operation_id), indent=2, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
