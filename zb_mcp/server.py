"""Typed MCP tools over shared Zenbot workflows, with private write receipts."""

from functools import wraps
import inspect
import json
import logging
from pathlib import Path
import time
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token
from fastmcp.tools.base import ToolResult
from pydantic import Field

from contracts import MemoryEntry, SessionSettingsInput
import storage_policy
from zb_services import ServiceError
from zb_services import conversations, koans, memories, reviews
from .auth import principal
from .operations import OperationStore

OperationId = Annotated[
    str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
]
RecordId = Annotated[
    str, Field(min_length=1, max_length=512, pattern=r"^[^/\\\x00-\x1f]+$")
]
Message = Annotated[str, Field(min_length=1, max_length=2048, pattern=r"\S")]
PageLimit = Annotated[int, Field(ge=1, le=100)]
Offset = Annotated[int, Field(ge=0, le=1_000_000)]
Index = Annotated[int, Field(ge=0)]
ReviewFilter = Literal[
    "all",
    "needs_cm_review",
    "needs_zb_review",
    "unreviewed",
    "not_started",
    "awaiting_other_review",
    "cm_reviewed",
    "zb_reviewed",
    "Use",
    "Alter",
    "Reject",
]

INSTRUCTIONS = """Private Zenbot tools for the Chief Monk.
Preserve each returned conversation_id, including a replacement ID returned by chat.
During dokusan, send the student's words exactly and relay Mumonbot's response
exactly, without commentary. Relay the closing bow and final response before archiving.
Load memory summaries before selecting full entries. Fetch a review record before
deciding it, and send both its index and conversation_id.
For every write, choose a unique operation_id and reuse it only for retries of
that same action with identical arguments. On running or uncertain outcomes,
use getOperationStatus; never evade the receipt by generating a new ID.
"""


def output_schema(name: str) -> dict[str, Any]:
    """Load the checked-in API output contract snapshot without a sibling-repo dependency."""
    document = json.loads(
        Path(__file__).with_name("output_schemas.json").read_text(encoding="utf-8")
    )

    def resolve(value):
        if isinstance(value, dict):
            result = (
                resolve(document["schemas"][value["$ref"].rsplit("/", 1)[-1]])
                if "$ref" in value
                else {}
            )
            result.update(
                {
                    key: resolve(item)
                    for key, item in value.items()
                    if key not in {"$ref", "nullable"}
                }
            )
            # The Actions document retains OpenAPI nullable syntax. MCP uses
            # JSON Schema, where nullable must become an explicit null branch.
            if value.get("nullable"):
                return {"anyOf": [result, {"type": "null"}]}
            return result
        if isinstance(value, list):
            return [resolve(item) for item in value]
        return value

    return resolve(document["operations"][name])


def create_server(
    operations: OperationStore, allowed_emails: frozenset[str], auth=None
) -> FastMCP:
    """Build a server; tests inject stores and tokens, production always supplies OAuth."""
    mcp = FastMCP(
        "Zenbot",
        instructions=INSTRUCTIONS,
        auth=auth,
        strict_input_validation=True,
        mask_error_details=True,
    )

    def register(
        name: str,
        *,
        write: bool = False,
        destructive: bool = False,
        open_world: bool = False,
    ):
        def decorate(function):
            signature = inspect.signature(function)

            @wraps(function)
            def invoke(*args, **kwargs):
                started = time.monotonic()
                operation_id = ""
                outcome = "failure"
                try:
                    subject = principal(get_access_token(), allowed_emails)
                    bound = signature.bind(*args, **kwargs)
                    bound.apply_defaults()
                    arguments = dict(bound.arguments)
                    operation_id = arguments.get("operation_id", "")
                    if write:
                        arguments.pop("operation_id")

                    def action():
                        with storage_policy.durable_storage():
                            return {"status": "success", **function(*args, **kwargs)}

                    if write:
                        # Internal model tools can modify shared memory/review data:
                        # one distributed write lock is intentional for this private v1.
                        canonical = json.loads(
                            json.dumps(
                                arguments,
                                default=lambda value: value.model_dump(mode="json"),
                            )
                        )
                        result = operations.execute(
                            subject,
                            operation_id,
                            name,
                            canonical,
                            ["monastery"],
                            action,
                        )
                    else:
                        result = action()
                except ServiceError as exc:
                    result = exc.payload()
                except (RuntimeError, OSError):
                    result = ServiceError(
                        "storage_unavailable",
                        "Required persistent storage is unavailable.",
                        503,
                    ).payload()
                except Exception:
                    result = ServiceError(
                        "internal_server_error", "Unexpected server-side failure.", 500
                    ).payload()
                outcome = result.get("error", result.get("status", "success"))
                logging.info(
                    "mcp_tool name=%s operation_id=%s duration_ms=%d outcome=%s",
                    name,
                    operation_id,
                    int((time.monotonic() - started) * 1000),
                    outcome,
                )
                # Exact chat response is the human-readable text; metadata stays structured.
                content = (
                    result.get("response")
                    if result.get("status") == "success"
                    else None
                )
                return ToolResult(
                    content=(
                        content
                        if content is not None
                        else json.dumps(result, ensure_ascii=False)
                    ),
                    structured_content=result,
                    is_error=result.get("status") == "failure",
                )

            return mcp.tool(
                name=name,
                output_schema=output_schema(name),
                annotations={
                    "readOnlyHint": not write,
                    "destructiveHint": destructive,
                    "openWorldHint": open_world,
                    "idempotentHint": write,
                },
                run_in_thread=True,
            )(invoke)

        return decorate

    @register("zbApiChatOptions")
    def chat_options() -> dict[str, Any]:
        """Read available models, presets, capabilities, and session settings."""
        return conversations.options()

    @register("zbApiChat", write=True, open_world=True, destructive=True)
    def chat(
        operation_id: OperationId,
        message: Message,
        conversation_id: str = "",
        student: str = "guest",
        settings: SessionSettingsInput | None = None,
        case_id: str = "",
    ) -> dict[str, Any]:
        """Send one exact dokusan turn. Reuse operation_id only to retry this turn."""
        return conversations.chat(message, conversation_id, student, settings, case_id)

    @register("zbApiChatCase", write=True)
    def start_case(
        operation_id: OperationId,
        case_id: RecordId,
        student: str = "api-case",
        settings: SessionSettingsInput | None = None,
    ) -> dict[str, Any]:
        """Start a koan-anchored session and return its conversation_id."""
        return conversations.start_case(case_id, student, settings)

    @register("zbApiSaveChat", write=True, destructive=True, open_world=True)
    def archive(operation_id: OperationId, conversation_id: RecordId) -> dict[str, Any]:
        """Archive after the final bow, update review records, and clear active state."""
        return conversations.archive(conversation_id)

    @register("listConversations")
    def list_conversations(
        offset: Offset = 0,
        limit: PageLimit = 25,
        case_id: str = "",
        student: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> dict[str, Any]:
        """List archived conversations with optional case, student, and date filters."""
        return conversations.list_archives(
            offset, limit, case_id, student, start_date, end_date
        )

    @register("getConversation")
    def get_conversation(conversation_id: RecordId) -> dict[str, Any]:
        """Fetch an archived transcript by conversation_id."""
        return conversations.get_archive(conversation_id)

    @register("searchExemplars")
    def search_exemplars(
        query: str, limit: Annotated[int, Field(ge=1, le=5)] = 3, case_id: str = ""
    ) -> dict[str, Any]:
        """Search accepted sessions for compact exemplar snippets."""
        return koans.search_exemplars(query.strip(), limit, case_id.strip())

    @register("loadMemoryLogbook")
    def load_memory(
        limit: Annotated[int, Field(ge=1, le=25)] = 12, end_index: Index | None = None
    ) -> dict[str, Any]:
        """Read newest-first memory summaries; select full entries afterward."""
        return memories.summaries(limit, end_index)

    @register("getMemoryLogbookEntry")
    def get_memory(serial_number: RecordId) -> dict[str, Any]:
        """Read one full canonical memory entry by serial number."""
        return memories.get_entry(serial_number)

    @register("saveMemoryCandidate", write=True)
    def candidate(operation_id: OperationId, entry: MemoryEntry) -> dict[str, Any]:
        """Queue a memory candidate for review without changing canonical memory."""
        return memories.candidate(entry)

    @register("commitMemoryEntry", write=True)
    def commit_memory(operation_id: OperationId, entry: MemoryEntry) -> dict[str, Any]:
        """Append one entry to the canonical logbook; serial numbers are resequenced."""
        return memories.commit(entry.model_dump(mode="json"))

    @register("replaceMemoryLogbook", write=True, destructive=True)
    def replace_memory(
        operation_id: OperationId, full_logbook: list[MemoryEntry]
    ) -> dict[str, Any]:
        """Replace the entire canonical memory logbook. This overwrites existing entries."""
        return memories.replace(
            [entry.model_dump(mode="json") for entry in full_logbook]
        )

    @register("getRandomKoan")
    def random_koan() -> dict[str, Any]:
        """Retrieve one randomly selected koan case."""
        return koans.random_koan()

    @register("getKoanByTitle")
    def koan_title(title: Annotated[str, Field(min_length=1)]) -> dict[str, Any]:
        """Find a koan by exact title or an unambiguous partial title."""
        return koans.by_title(title.strip())

    @register("getKoanById")
    def koan_id(
        case_id: RecordId, include_solution_notes: bool = False
    ) -> dict[str, Any]:
        """Retrieve a koan by ID, optionally including available solution notes."""
        return koans.by_id(case_id, include_solution_notes)

    @register("getSessionEvaluationSummary")
    def review_summary() -> dict[str, Any]:
        """Read review counts, progress, and storage metadata."""
        return reviews.summary()

    @register("getSessionEvaluationRecord")
    def review_record(index: Index) -> dict[str, Any]:
        """Read a full review record before deciding it."""
        return reviews.record(index)

    @register("listSessionEvaluationsNeedingZbReview")
    def needing_review(offset: Offset = 0, limit: PageLimit = 25) -> dict[str, Any]:
        """List compact records awaiting Zenbot's review."""
        return reviews.needs_review(offset, limit)

    @register("getRandomSessionEvaluationForZbReview")
    def random_review(exclude_indices: list[Index] | None = None) -> dict[str, Any]:
        """Choose a full pending review, optionally excluding already-seen indices."""
        return reviews.random_review(set(exclude_indices or []))

    @register("enqueueReview", write=True)
    def enqueue_review(
        operation_id: OperationId,
        conversation_id: RecordId,
        reviewer: Literal["ZB", "CM"] = "CM",
        note: str = "",
    ) -> dict[str, Any]:
        """Queue a follow-up review for a conversation."""
        return reviews.enqueue(conversation_id, reviewer, note)

    @register("setSessionEvaluationDecision", write=True, destructive=True)
    def decide(
        operation_id: OperationId,
        index: Index,
        conversation_id: RecordId,
        evaluation: Literal["Use", "Alter", "Reject"],
        reviewer: Literal["ZB", "CM"] = "ZB",
    ) -> dict[str, Any]:
        """Save a review decision only if the index still matches conversation_id."""
        return reviews.decision(index, evaluation, reviewer, conversation_id)

    @register("getRandomSessionEvaluationSample")
    def sample(
        count: Annotated[int, Field(ge=1, le=25)] = 3,
        exclude_indices: list[Index] | None = None,
    ) -> dict[str, Any]:
        """Read a unique random sample of compact review records."""
        return reviews.sample(count, set(exclude_indices or []))

    @register("getNextSessionEvaluationRecord")
    def next_review(
        evaluation: ReviewFilter = "unreviewed", after: int = -1
    ) -> dict[str, Any]:
        """Find the next review record matching a review state."""
        return reviews.next_record(evaluation, after)

    @register("reportRuntimeStatus", write=True)
    def report_status(
        operation_id: OperationId,
        stage: Annotated[str, Field(min_length=1)],
        detail: str = "",
    ) -> dict[str, Any]:
        """Report a runtime breadcrumb without writing its detail to server logs."""
        return reviews.report_status(stage, detail)

    @register("getOperationStatus")
    def operation_status(operation_id: OperationId) -> dict[str, Any]:
        """Inspect your operation's receipt after a retry, timeout, or disconnection."""
        return operations.status(
            principal(get_access_token(), allowed_emails), operation_id
        )

    return mcp
