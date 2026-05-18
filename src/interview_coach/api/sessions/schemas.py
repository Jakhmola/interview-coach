import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RoundType(StrEnum):
    resume_walkthrough = "resume_walkthrough"
    behavioral_star = "behavioral_star"


class SessionStatus(StrEnum):
    active = "active"
    complete = "complete"
    abandoned = "abandoned"


class SessionCreateRequest(BaseModel):
    job_id: uuid.UUID
    round_type: RoundType
    n_questions: int = Field(default=5, ge=1, le=20)


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    job_id: uuid.UUID
    round_type: RoundType
    status: SessionStatus
    n_questions: int
    created_at: datetime


class TurnOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    turn_index: int
    question: str
    anchors_json: list[str]
    answer: str | None = None
    score: int | None = None
    feedback: str | None = None
    model_answer: str | None = None
    metadata_json: dict[str, Any] | None = None
    created_at: datetime


class SessionDetail(SessionOut):
    turns: list[TurnOut]


class AnswerSubmitRequest(BaseModel):
    answer: str


class PrepareRequest(BaseModel):
    job_id: uuid.UUID
    force_refresh: bool = False


class PrepStatusOut(BaseModel):
    job_id: uuid.UUID
    has_cv: bool
    profile_ready: bool
    job_analyzed: bool
    company_researched: bool
    can_start: bool
    missing: list[str]
    profile: dict[str, Any] | None = None
    job: dict[str, Any] | None = None
    company: dict[str, Any] | None = None


# --- prep_graph mapping-resume payload (Phase 21.1) -------------------


class PrepareMappingRow(BaseModel):
    """One row of the user's mapping decision. Mirrors what
    ``apply_mapping`` already accepts internally."""

    mapping_kind: Literal["highlight", "experience", "project"]
    experience_idx: int | None = None
    highlight_idx: int | None = None
    project_idx: int | None = None


class PrepareMappingExtracted(BaseModel):
    """The ``extracted`` payload from the intake LLM call. The FE
    forwards the dict it received in the prior ``mapping_suggestion``
    SSE event so the user's edits to tech/urls are preserved."""

    tech_stack: list[str] = Field(default_factory=list)
    description: str | None = None
    urls: list[str] = Field(default_factory=list)


class PrepareMappingResumeRequest(BaseModel):
    """User's response to a paused ``mapping_suggestion``. Sent to
    ``POST /sessions/prepare/resume``; LangGraph threads it back into
    the ``await_mapping_confirm`` interrupt and the prep_graph advances.
    """

    job_id: uuid.UUID
    action: Literal["apply", "skip"]
    # Only required for action="apply":
    rows: list[PrepareMappingRow] = Field(default_factory=list)
    title: str | None = None
    extracted: PrepareMappingExtracted | None = None
