"""Typed runtime contracts and tool schemas for the Zenbot v3 runtime.

These models serve two purposes:

- make the live application code less dependent on untyped ad hoc dict payloads
- provide one stable schema source for OpenAI Responses tools and API docs

The models are intentionally conservative and strict. They mirror the shapes
already moving through the app today while giving the new Responses-based
runtime a typed foundation for v3 work.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """Base class for strict application contracts."""

    model_config = ConfigDict(extra="forbid")


class ConversationMessage(StrictModel):
    """One conversation message stored in live or archived session state."""

    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1)


class SessionSettingsInput(StrictModel):
    """Partial session settings supplied when starting or continuing a session."""

    preset_id: str = ""
    model_name: str = ""
    reasoning_effort: str = ""
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, gt=0.0, le=1.0)
    max_output_tokens: int | None = Field(default=None, ge=1, le=4096)
    enable_function_tools: bool | None = None
    enable_file_search: bool | None = None
    enable_web_search: bool | None = None
    enable_background_critic: bool | None = None


class ResolvedSessionSettings(StrictModel):
    """Canonical resolved settings snapshot locked to one conversation."""

    preset_id: str
    model_name: str
    reasoning_effort: str = ""
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, gt=0.0, le=1.0)
    max_output_tokens: int = Field(ge=1, le=4096)
    enable_function_tools: bool = False
    enable_file_search: bool = False
    enable_web_search: bool = False
    enable_background_critic: bool = False


class SessionPresetOption(StrictModel):
    """One named Mumonbot-ling preset exposed in the UI/API."""

    id: str
    label: str
    description: str
    settings: ResolvedSessionSettings


class SessionModelOption(StrictModel):
    """One available model plus the controls it supports."""

    id: str
    label: str
    supports_reasoning: bool
    reasoning_efforts: list[str] = Field(default_factory=list)
    supports_sampling_controls: bool


class SessionToolCaps(StrictModel):
    """Deployment-level capability caps for per-session tool toggles."""

    enable_function_tools: bool = False
    enable_file_search: bool = False
    enable_web_search: bool = False
    enable_background_critic: bool = False


class SessionOptionsPayload(StrictModel):
    """Canonical session-settings options payload for browser/API clients."""

    settings_version: str
    defaults: ResolvedSessionSettings
    presets: list[SessionPresetOption] = Field(default_factory=list)
    models: list[SessionModelOption] = Field(default_factory=list)
    tool_caps: SessionToolCaps


class TurnRequest(StrictModel):
    """Validated request payload for one user-facing chat turn."""

    message: str = Field(min_length=1, max_length=2048)
    conversation_id: str = ""
    student: str = "webmonkE"
    settings: SessionSettingsInput | None = None


class SessionStartRequest(StrictModel):
    """Validated request payload for creating a new session before any turn."""

    student: str = "webmonkE"
    settings: SessionSettingsInput | None = None


class ToolCallResult(StrictModel):
    """Structured result returned from one internal runtime tool call."""

    name: str
    ok: bool = True
    payload: dict[str, Any] = Field(default_factory=dict)


class SessionEvaluation(StrictModel):
    """Structured evaluation data attached to one memory or review record."""

    case: str
    conversation_id: str
    evaluation: str
    notes: str


class MemoryEntry(StrictModel):
    """Canonical structured memory entry for the Zenbot logbook."""

    date: str
    time: str
    serial_number: str
    title: str
    koans_used: list[str]
    user_problem_or_questions: str
    response_summary: str
    session_evaluations: list[SessionEvaluation]
    key_insights: list[str]
    lessons_learned: list[str]
    final_outcome: str
    user_instructions: list[str] = Field(default_factory=list)


class ReviewMetadata(StrictModel):
    """Metadata emitted with one review record envelope."""

    conversation_id: str
    transcript_blob_name: str
    transcript_hash: str
    message_count: int
    saved_at: str | None = None
    case_id: str | None = None
    profile: str | None = None
    loss: Any | None = None
    model: str | None = None
    student: str | None = None
    botling_id: str | None = None
    settings_version: str | None = None
    session_settings: dict[str, Any] | None = None


class ReviewNavigation(StrictModel):
    """Navigation metadata for the session-review UI and API."""

    previous_index: int
    next_index: int
    next_unreviewed_index: int | None = None


class ReviewRecord(StrictModel):
    """Full review record envelope returned by review API routes."""

    index: int
    display_number: int
    evaluation: str | None = None
    status_label: str
    status_class: str
    review_version: int
    review_zb: Literal["Use", "Alter", "Reject"] | None = None
    review_cm: Literal["Use", "Alter", "Reject"] | None = None
    preview_user: str = ""
    preview_assistant: str = ""
    messages: list[ConversationMessage]
    metadata: ReviewMetadata
    summary: dict[str, int]
    navigation: ReviewNavigation


class TrainingExample(StrictModel):
    """Conversation-level training example exported for dataset building."""

    messages: list[ConversationMessage]
    source: str = ""
    conversation_id: str = ""
    case_id: str = ""


class VectorStoreDoc(StrictModel):
    """Normalized document payload suitable for vector-store sync scripts."""

    document_id: str
    source: str
    title: str
    text: str
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)


class LoadCaseContextArgs(StrictModel):
    """Arguments for the koan-context lookup tool."""

    case_id: str
    include_commentary: bool = False


class SearchExemplarsArgs(StrictModel):
    """Arguments for the approved-exemplar retrieval tool."""

    query: str
    limit: int = Field(default=3, ge=1, le=5)
    case_id: str = ""


class LoadMemorySummariesArgs(StrictModel):
    """Arguments for the compact memory-summary lookup tool."""

    limit: int = Field(default=5, ge=1, le=12)


class LoadMemoryEntryArgs(StrictModel):
    """Arguments for the full memory-entry lookup tool."""

    serial_number: str


class SaveMemoryCandidateArgs(StrictModel):
    """Arguments for queueing a structured memory candidate."""

    entry: MemoryEntry


class ArchiveSessionArgs(StrictModel):
    """Arguments for the archive-session tool."""

    conversation_id: str


class EnqueueReviewArgs(StrictModel):
    """Arguments for queueing a review follow-up request."""

    conversation_id: str
    note: str = ""
    reviewer: Literal["ZB", "CM"] = "CM"


class ReportUiStatusArgs(StrictModel):
    """Arguments for the UI status reporting tool."""

    stage: str
    detail: str = ""
