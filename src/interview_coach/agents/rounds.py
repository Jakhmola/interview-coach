"""Round-strategy registry (Phase 33).

A "round type" used to be a bare string the picker, prompt renderer, and
evaluator each branched on with ``if/elif round_type == ...``. This module
makes the round a first-class object: one :class:`RoundStrategy` per round
declaring the four policies that actually differ between rounds:

* ``focus`` — which corpus the focus picker draws candidates from.
* ``question_system`` — the question-generation system prompt.
* ``qgen_grounding`` — whether to retrieve repo code at question-gen time
  (only the experience round, and only for a repo-backed focus).
* ``answer_grounding`` + ``model_answer_system`` — how the evaluator's
  model-answer call is grounded and which prompt writes it.

The shared judge prompt stays out of the strategy: it is round-agnostic
(the per-turn anchors drive calibration), so it doesn't vary here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from interview_coach.agents.prompts import (
    MODEL_ANSWER_BEHAVIORAL_SYSTEM,
    MODEL_ANSWER_SYSTEM,
    MODEL_ANSWER_TECHNICAL_SYSTEM,
    QUESTION_BEHAVIORAL_STAR_SYSTEM,
    QUESTION_EXPERIENCE_SYSTEM,
    QUESTION_TECHNICAL_SYSTEM,
)


class FocusMode(StrEnum):
    """Which corpus the focus picker scores candidates from."""

    experience_projects = "experience_projects"  # CV highlights + projects
    jd_skills = "jd_skills"  # the role's must-have skills
    behavioral_signals = "behavioral_signals"  # JD signals → company values


class AnswerGrounding(StrEnum):
    """How the evaluator's model-answer call is grounded."""

    rag_docs = "rag_docs"  # retrieve project_doc + github_repo, scoped to focus
    none_authoritative = "none_authoritative"  # correct reference answer, no RAG
    none_hypothetical = "none_hypothetical"  # illustrative example, no RAG


@dataclass(frozen=True)
class RoundStrategy:
    value: str
    focus: FocusMode
    question_system: str
    qgen_grounding: bool
    answer_grounding: AnswerGrounding
    model_answer_system: str
    # Phase-34 slot, intentionally unused this phase:
    # max_followups: int = 0


ROUND_STRATEGIES: dict[str, RoundStrategy] = {
    "experience_deep_dive": RoundStrategy(
        value="experience_deep_dive",
        focus=FocusMode.experience_projects,
        question_system=QUESTION_EXPERIENCE_SYSTEM,
        qgen_grounding=True,
        answer_grounding=AnswerGrounding.rag_docs,
        model_answer_system=MODEL_ANSWER_SYSTEM,
    ),
    "technical_challenge": RoundStrategy(
        value="technical_challenge",
        focus=FocusMode.jd_skills,
        question_system=QUESTION_TECHNICAL_SYSTEM,
        qgen_grounding=False,
        answer_grounding=AnswerGrounding.none_authoritative,
        model_answer_system=MODEL_ANSWER_TECHNICAL_SYSTEM,
    ),
    "behavioral_star": RoundStrategy(
        value="behavioral_star",
        focus=FocusMode.behavioral_signals,
        question_system=QUESTION_BEHAVIORAL_STAR_SYSTEM,
        qgen_grounding=False,
        answer_grounding=AnswerGrounding.none_hypothetical,
        model_answer_system=MODEL_ANSWER_BEHAVIORAL_SYSTEM,
    ),
}


def get_round_strategy(round_type: str) -> RoundStrategy:
    """Return the strategy for ``round_type``; raise ``ValueError`` if unknown.

    The schema (DB CHECK + ``RoundType`` enum) already gates the value, so an
    unknown string here means a programming error, not bad user input.
    """
    try:
        return ROUND_STRATEGIES[round_type]
    except KeyError:
        raise ValueError(f"unknown round_type: {round_type!r}") from None
