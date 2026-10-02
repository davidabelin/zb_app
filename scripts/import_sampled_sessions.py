"""Dry-run or import historical Dokusan archives into private sampled storage."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from sampled_sessions import import_historical  # noqa: E402


def main() -> int:
    """Use application-default credentials; leave all sources and admin data intact."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=APP_ROOT.parent)
    parser.add_argument("--bucket", default="zenbot-434517-sampled-sessions")
    parser.add_argument("--project", default="zenbot-434517")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Upload missing historical recordings; default is read-only dry-run",
    )
    args = parser.parse_args()
    directories = [
        args.repo_root / "collected_sessions" / name for name in ("local", "web", "use")
    ]
    directories.append(args.repo_root / "zb_app" / "config" / "zbchats")
    if not all(path.is_dir() for path in directories[:3]):
        parser.error(
            "Expected collected_sessions/local, web, and use beneath --repo-root"
        )
    try:
        from google.cloud import storage

        bucket = storage.Client(project=args.project).bucket(args.bucket)
        # Do not place identity-bearing originals into a publicly readable bucket.
        bucket.reload()
        if (
            not bucket.iam_configuration.uniform_bucket_level_access_enabled
            or bucket.iam_configuration.public_access_prevention != "enforced"
        ):
            raise RuntimeError(
                "Historical imports require uniform bucket-level access and "
                "enforced public access prevention"
            )
        policy = bucket.get_iam_policy(requested_policy_version=3)
        if any(
            member in {"allUsers", "allAuthenticatedUsers"}
            for binding in policy.bindings
            for member in binding["members"]
        ):
            raise RuntimeError("Historical imports require a private bucket")
        report = import_historical(bucket, directories, apply=args.apply)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print(
            f"Import failed ({type(exc).__name__}); check credentials, private bucket access, and source files.",
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps({"mode": "apply" if args.apply else "dry-run", **report}, indent=2)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
