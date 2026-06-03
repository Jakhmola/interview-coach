"""Round-strategy registry (Phase 33).

A "round type" used to be a bare string the picker, prompt renderer, and
evaluator each branched on with ``if/elif round_type == ...``. This module
makes the round a first-class object: one :class:`RoundStrategy` per round
declaring the four policies that actually differ between rounds:

* ``focus`` — which corpus the focus picker draws candidates from.
* ``question_system`` — the question-generation (thread-open) system prompt.
* ``qgen_grounding`` — whether to retrieve repo code at thread-open
  (only the experience round, and only for a repo-backed focus).
* ``answer_grounding`` + ``model_answer_system`` — how the evaluator's
  model-answer call is grounded and which prompt writes it.
* ``allow_nudge`` + ``max_followups`` (Phase 34) — the per-round conductor
  policy. ``allow_nudge`` gates whether ``nudge`` is offered to the conductor
  at all (behavioral never nudges — a STAR answer is the candidate's to
  shape). ``max_followups`` is a single combined hard cap over
  probe+clarify+nudge per thread — the safety net guaranteeing a thread
  terminates (``question``/``advance`` don't count against it).

The shared judge prompt stays out of the strategy: it is round-agnostic
(the thread's anchors drive calibration), so it doesn't vary here. The
conductor system prompt (``CONDUCTOR_SYSTEM``) is likewise shared — the
per-round flavor it needs rides in via ``allow_nudge`` and the focus.
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
    # Phase 34 conductor policy:
    allow_nudge: bool = True
    max_followups: int = 3


ROUND_STRATEGIES: dict[str, RoundStrategy] = {
    "experience_deep_dive": RoundStrategy(
        value="experience_deep_dive",
        focus=FocusMode.experience_projects,
        question_system=QUESTION_EXPERIENCE_SYSTEM,
        qgen_grounding=True,
        answer_grounding=AnswerGrounding.rag_docs,
        model_answer_system=MODEL_ANSWER_SYSTEM,
        allow_nudge=True,
        max_followups=3,
    ),
    "technical_challenge": RoundStrategy(
        value="technical_challenge",
        focus=FocusMode.jd_skills,
        question_system=QUESTION_TECHNICAL_SYSTEM,
        qgen_grounding=False,
        answer_grounding=AnswerGrounding.none_authoritative,
        model_answer_system=MODEL_ANSWER_TECHNICAL_SYSTEM,
        allow_nudge=True,
        max_followups=3,
    ),
    "behavioral_star": RoundStrategy(
        value="behavioral_star",
        focus=FocusMode.behavioral_signals,
        question_system=QUESTION_BEHAVIORAL_STAR_SYSTEM,
        qgen_grounding=False,
        answer_grounding=AnswerGrounding.none_hypothetical,
        model_answer_system=MODEL_ANSWER_BEHAVIORAL_SYSTEM,
        # Behavioral never nudges — a STAR answer is the candidate's to shape;
        # and the round caps at a single follow-up.
        allow_nudge=False,
        max_followups=1,
    ),
}


def conductor_allowed_actions(strategy: RoundStrategy) -> list[str]:
    """The move set the conductor may pick from for this round.

    ``advance`` is always allowed (the conductor can decide a topic is done);
    ``probe`` and ``clarify`` are always offered; ``nudge`` only when the round
    permits it. The graph's budget guard can still force ``advance`` regardless
    of what the model returns.
    """
    actions = ["probe", "clarify"]
    if strategy.allow_nudge:
        actions.append("nudge")
    actions.append("advance")
    return actions


def get_round_strategy(round_type: str) -> RoundStrategy:
    """Return the strategy for ``round_type``; raise ``ValueError`` if unknown.

    The schema (DB CHECK + ``RoundType`` enum) already gates the value, so an
    unknown string here means a programming error, not bad user input.
    """
    try:
        return ROUND_STRATEGIES[round_type]
    except KeyError:
        raise ValueError(f"unknown round_type: {round_type!r}") from None
