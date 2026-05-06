"""Pydantic schemas the agents extract from documents and JDs.

`Profile` is the structured candidate snapshot built by ProfileBuilder.
`JobAnalysis` is the structured JD breakdown built by JobAnalyzer.

These are the single source of truth for the shape downstream agents
(QuestionGenerator, Evaluator) consume.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Seniority(StrEnum):
    junior = "junior"
    mid = "mid"
    senior = "senior"
    staff = "staff"
    principal = "principal"
    unknown = "unknown"


class Experience(BaseModel):
    company: str
    role: str
    start: str | None = Field(default=None, description="Free-form, e.g. '2021' or 'Mar 2021'")
    end: str | None = Field(default=None, description="Same format as start; 'present' if current")
    highlights: list[str] = Field(default_factory=list)


class ProjectItem(BaseModel):
    name: str
    description: str
    tech: list[str] = Field(default_factory=list)
    role: str | None = None


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
