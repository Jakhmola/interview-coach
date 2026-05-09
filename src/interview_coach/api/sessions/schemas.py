import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

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
