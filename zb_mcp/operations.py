"""Transactional Firestore claims and fail-closed recovery for MCP writes.

Only the claim/record transactions are retried. The side-effecting callback is
never executed inside a Firestore transaction or retried automatically.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any, Callable

from google.cloud import firestore

from zb_services import ServiceError

RETENTION = timedelta(days=7)
STALE_AFTER = timedelta(minutes=10)


def digest(value: Any) -> str:
    """Hash canonical JSON, including message whitespace, without storing inputs."""
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()


class OperationStore:
    """Persist claims and resource locks across processes and restarts."""

    def __init__(
        self,
        client: Any,
        namespace: str = "zenbot_mcp",
        clock: Callable[[], datetime] | None = None,
    ):
        self.client = client
        self.records = client.collection(f"{namespace}_operations")
        self.locks = client.collection(f"{namespace}_locks")
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def reference(self, principal: str, operation_id: str):
        """Namespace caller-selected IDs by authenticated Google subject."""
        return self.records.document(digest([principal, operation_id]))

    def claim(
        self,
        principal: str,
        operation_id: str,
        tool: str,
        arguments: dict[str, Any],
        resources: list[str],
    ) -> tuple[bool, dict[str, Any]]:
        """Atomically claim a write and all resources, or return its prior result."""
        ref = self.reference(principal, operation_id)
        fingerprint = digest([tool, arguments])
        now = self.clock()
        lock_refs = [self.locks.document(digest(key)) for key in sorted(set(resources))]

        @firestore.transactional
        def commit(transaction):
            previous = ref.get(transaction=transaction).to_dict()
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise ServiceError(
                        "operation_id_conflict",
                        "This operation_id was used with different arguments.",
                        409,
                    )
                # Never automatically reuse an expired ID while its receipt exists.
                return False, previous
            locks = [lock.get(transaction=transaction).to_dict() for lock in lock_refs]
            if any(locks):
                raise ServiceError(
                    "resource_busy",
                    "A write or unresolved operation owns this resource. Inspect its operation before retrying.",
                    409,
                )
            record = {
                "principal": principal,
                "operation_id": operation_id,
                "tool": tool,
                "fingerprint": fingerprint,
                "state": "running",
                "started_at": now,
                "resources": sorted(set(resources)),
            }
            transaction.set(ref, record)
            for lock in lock_refs:
                transaction.set(lock, {"operation": ref.id, "started_at": now})
            return True, record

        return commit(self.client.transaction())

    def finish(
        self,
        principal: str,
        operation_id: str,
        result: dict[str, Any],
        state: str = "completed",
    ) -> None:
        """Store a receipt and release locks only for a confirmed completion."""
        ref = self.reference(principal, operation_id)

        @firestore.transactional
        def commit(transaction):
            row = ref.get(transaction=transaction).to_dict()
            if not row or row["state"] != "running":
                raise RuntimeError(
                    "Operation claim was lost; reconciliation is required."
                )
            locks = [self.locks.document(digest(key)) for key in row["resources"]]
            owners = [lock.get(transaction=transaction).to_dict() for lock in locks]
            if any(not owner or owner["operation"] != ref.id for owner in owners):
                raise RuntimeError("Operation resource lock was lost.")
            updated = {
                **row,
                "state": state,
                "result": result,
                "updated_at": self.clock(),
            }
            if state == "completed":
                updated["expires_at"] = self.clock() + RETENTION
                for lock in locks:
                    transaction.delete(lock)
            transaction.set(ref, updated)

        commit(self.client.transaction())

    def status(self, principal: str, operation_id: str) -> dict[str, Any]:
        """Return a caller's receipt; old running claims are uncertain, never stealable."""
        row = self.reference(principal, operation_id).get().to_dict()
        if not row:
            raise ServiceError(
                "operation_not_found",
                "No operation exists for this account and ID.",
                404,
            )
        state = row["state"]
        if state == "running" and self.clock() - row["started_at"] > STALE_AFTER:
            state = "uncertain"
        result = {
            "operation_id": operation_id,
            "tool": row["tool"],
            "state": state,
            "started_at": row["started_at"].isoformat(),
        }
        if "result" in row:
            result["result"] = row["result"]
        return result

    def execute(
        self,
        principal: str,
        operation_id: str,
        tool: str,
        arguments: dict[str, Any],
        resources: list[str],
        action: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        """Run at most once per retained claim; block replay after uncertain effects."""
        claimed, row = self.claim(principal, operation_id, tool, arguments, resources)
        if not claimed:
            if row["state"] == "completed":
                return row["result"]
            state = self.status(principal, operation_id)["state"]
            raise ServiceError(
                f"operation_{state}",
                "Inspect getOperationStatus; do not retry with a new operation_id.",
                409,
                operation_id=operation_id,
            )
        try:
            result = action()
        except Exception as exc:
            if isinstance(exc, ServiceError) and not exc.effects_possible:
                # A known rejection before side effects is a terminal receipt.
                result = exc.payload()
            else:
                failure = {
                    "status": "failure",
                    "error": "operation_uncertain",
                    "message": "Execution did not complete. Reconcile this operation before another write.",
                    "operation_id": operation_id,
                }
                try:
                    self.finish(principal, operation_id, failure, state="uncertain")
                except Exception:
                    pass  # The original durable running claim still prevents replay.
                raise ServiceError(
                    "operation_uncertain",
                    failure["message"],
                    409,
                    operation_id=operation_id,
                ) from None
        try:
            self.finish(principal, operation_id, result)
        except Exception:
            raise ServiceError(
                "operation_uncertain",
                "The action may have completed, but its receipt was not confirmed. Inspect before retrying.",
                409,
                operation_id=operation_id,
            ) from None
        return result

    def reconcile(
        self, principal: str, operation_id: str, result: dict[str, Any], note: str
    ) -> None:
        """Record an operator-verified outcome after the original worker is stopped."""
        if result.get("status") not in {"success", "failure"} or not note.strip():
            raise ValueError(
                "A success/failure result and an operator note are required."
            )
        ref = self.reference(principal, operation_id)

        @firestore.transactional
        def commit(transaction):
            row = ref.get(transaction=transaction).to_dict()
            if not row or row["state"] == "completed":
                raise ValueError("Only unresolved operations can be reconciled.")
            locks = [self.locks.document(digest(key)) for key in row["resources"]]
            owners = [lock.get(transaction=transaction).to_dict() for lock in locks]
            if any(not owner or owner["operation"] != ref.id for owner in owners):
                raise ValueError(
                    "A resource lock is missing or owned by another operation."
                )
            now = self.clock()
            transaction.set(
                ref,
                {
                    **row,
                    "state": "completed",
                    "result": result,
                    "updated_at": now,
                    "expires_at": now + RETENTION,
                    "reconciliation": {
                        "note": note.strip(),
                        "at": now,
                        "previous_state": row["state"],
                    },
                },
            )
            for lock in locks:
                transaction.delete(lock)

        commit(self.client.transaction())
