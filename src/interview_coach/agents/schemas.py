"""Pydantic schemas the agents extract from documents and JDs.

`Profile` is the structured candidate snapshot built by ProfileBuilder.
`JobAnalysis` is the structured JD breakdown built by JobAnalyzer.

These are the single source of truth for the shape downstream agents
(QuestionGenerator, Evaluator) consume.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class Seniority(StrEnum):
    junior = "junior"
    mid = "mid"
    senior = "senior"
    staff = "staff"
    principal = "principal"
    unknown = "unknown"


class Highlight(BaseModel):
    """One resume bullet, optionally enriched by a project_doc upload.

    `text` is the canonical CV bullet — never mutated by enrichment.
    Enrichment fields (`tech_stack`, `description`, `urls`) accumulate
    from `document_mappings` rows; `source_document_ids` records which
    docs contributed so deletion can revert precisely.
    """

    text: str
    tech_stack: list[str] = Field(default_factory=list)
    description: str | None = None
    urls: list[str] = Field(default_factory=list)
    source_document_ids: list[uuid.UUID] = Field(default_factory=list)


class Experience(BaseModel):
    company: str
    role: str
    start: str | None = Field(default=None, description="Free-form, e.g. '2021' or 'Mar 2021'")
    end: str | None = Field(default=None, description="Same format as start; 'present' if current")
    highlights: list[Highlight] = Field(default_factory=list)


class ProjectItem(BaseModel):
    """A standalone project NOT tied to an Experience row.

    Project docs that describe work-at-a-company enrich an Experience
    highlight instead of creating a ProjectItem. `source='github'` is a
    repo ingested by the Phase 32 GitHub crawler (folded into the Profile
    by the github prep-graph node); `source='manual'` for user-added
    entries we don't yet have a UI for.
    """

    name: str
    description: str
    tech: list[str] = Field(default_factory=list)
    role: str | None = None
    urls: list[str] = Field(default_factory=list)
    # Phase 32 follow-up: richer github extraction surfaced to the interview
    # (resume deep-dive / question generator) and the Manage UI. Defaulted so
    # CV / project_doc / manual projects and every already-persisted profile
    # stay valid, and older github ``parsed_json`` still ``model_validate``s.
    key_features: list[str] = Field(default_factory=list)
    architecture: str | None = None
    source: Literal["project_doc", "github", "manual"] = "project_doc"
    source_document_ids: list[uuid.UUID] = Field(default_factory=list)


class Education(BaseModel):
    school: str
    degree: str
    start: str | None = None
    end: str | None = None


class Profile(BaseModel):
    """Candidate profile extracted from CV + project docs."""

    summary: str = Field(description="One-paragraph elevator pitch in candidate's voice")
    skills: list[str] = Field(default_factory=list)
    experiences: list[Experience] = Field(default_factory=list)
    projects: list[ProjectItem] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)


class JobAnalysis(BaseModel):
    """Structured breakdown of a JD."""

    title: str
    seniority: Seniority = Seniority.unknown
    must_have_skills: list[str] = Field(default_factory=list)
    nice_to_have_skills: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    behavioral_signals: list[str] = Field(
        default_factory=list,
        description=(
            "Soft-skill / behavioral competencies the role implies "
            "(e.g., 'cross-team communication', 'ownership', 'mentorship'). "
            "Phase 8 question generator picks from these for STAR prompts."
        ),
    )
    company_name: str | None = None


class Question(BaseModel):
    """One interview question + the rubric anchors Phase 9 will score against."""

    question: str = Field(description="The question text in interviewer's voice.")
    anchors: list[str] = Field(
        description=(
            "3–5 evaluation anchors: concrete things a strong answer would cover. "
            "Used by the Phase 9 evaluator as scoring rubric."
        ),
    )


class Evaluation(BaseModel):
    """Combined evaluator output for a single turn (Phase 9).

    Phase 14 splits the LLM call into two — `Judgment` and `ModelAnswer` —
    but the persisted shape and external API still match this combined
    schema, so we keep it.
    """

    score: int = Field(ge=1, le=10, description="Single overall 1–10 score.")
    feedback: str = Field(description="Concise paragraph explaining the score.")
    model_answer: str = Field(
        description=(
            "A strong reference answer written in first person, in the "
            "candidate's voice, grounded in their profile."
        ),
    )


class Judgment(BaseModel):
    """Phase 14 judge-call output: score + feedback only."""

    score: int = Field(ge=1, le=10)
    feedback: str


class ModelAnswerOnly(BaseModel):
    """Phase 14 model-answer call output."""

    model_answer: str


class DocIntakeExtracted(BaseModel):
    """What a project_doc contributes when mapped onto profile."""

    tech_stack: list[str] = Field(default_factory=list)
    description: str | None = None
    urls: list[str] = Field(default_factory=list)


class DocIntakeSuggestion(BaseModel):
    """One mapping suggestion from the LLM (HITL-confirmed downstream)."""

    mapping_kind: Literal["highlight", "experience", "project"]
    experience_idx: int | None = None
    highlight_idx: int | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""


class DocIntakeResult(BaseModel):
    """Combined output of the project_doc intake LLM call."""

    title: str = Field(description="Short project title, max ~80 chars.")
    extracted: DocIntakeExtracted
    suggestions: list[DocIntakeSuggestion] = Field(default_factory=list)


class GithubProjectExtract(BaseModel):
    """Phase 32: what the LLM pulls from a repo's README + manifests.

    The github prep-graph node assembles a full ``ProjectItem`` around this
    (``name`` ← repo, ``urls`` ← repo URL, ``source='github'``). Keeping the
    LLM-facing schema narrow — just description + tech — sidesteps per-ecosystem
    manifest parsing: the model reads ``pyproject.toml`` / ``package.json`` /
    etc. as raw text and names the real frameworks. No ``role`` is extracted —
    a public repo rarely states one and inventing it adds noise.
    """

    description: str = Field(
        description=(
            "A detailed 2–3 sentence project description in the candidate's first-person "
            "voice: what the project does, the problem it solves, and how it is built "
            "(key components / architecture), grounded in the README + manifests."
        )
    )
    tech: list[str] = Field(
        default_factory=list,
        description=(
            "Concrete frameworks/libraries/tools the repo uses, read from manifests + "
            "infra files. Real names (e.g. 'fastapi', 'pgvector'), not just "
            "languages-by-bytes."
        ),
    )
    key_features: list[str] = Field(
        default_factory=list,
        description=(
            "3-5 concrete capabilities or components the repo delivers, grounded in "
            "the README + manifests (e.g. 'JWT auth', 'pgvector retrieval', "
            "'streaming SSE API'). Empty when the material doesn't evidence any — "
            "never fabricate."
        ),
    )
    architecture: str | None = Field(
        default=None,
        description=(
            "One sentence on how the project is built / wired together "
            "(e.g. 'FastAPI backend + React frontend behind a local llama.cpp "
            "model, grounded by a pgvector layer'). Null when not evidenced."
        ),
    )


class CompanySnapshot(BaseModel):
    """LLM-compressed view of a company, used by Phase 8 question generation."""

    mission: str = Field(description="One-paragraph company mission / what they do.")
    products: list[str] = Field(
        default_factory=list,
        description="Main products / business lines, short phrases.",
    )
    recent_news: list[str] = Field(
        default_factory=list,
        description="Notable recent news items, each one sentence; max 5.",
    )
    values_and_signals: list[str] = Field(
        default_factory=list,
        description=(
            "Cultural values + interview signals candidates should be ready for "
            "(e.g. 'customer obsession', 'high autonomy', 'written-doc culture'). "
            "Drives behavioral question selection downstream."
        ),
    )
