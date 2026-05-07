"""Shared TypedDict state for the LangGraph supervisor (Phase 10).

Two compiled graphs share this same state:

* ``prep_graph`` — runs ProfileBuilder → JobAnalyzer → CompanyResearcher.
  Reads ``user_id``, ``job_id``, ``force_refresh``; sets ``profile``,
  ``job``, ``company``, ``prep_done``.
* ``interview_graph`` — runs QuestionGenerator → (interrupt) → Evaluator,
  looping until the session completes. Reads ``user_id``, ``session_id``,
  ``round_type``, ``n_questions``; mutates ``current_question``,
  ``current_answer``, ``evaluation``, ``turn_index``, ``session_status``.

Both graphs use ``next_step`` for observability (Langfuse Phase 11) and
to make the conditional-edge routing self-documenting.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

RoundType = Literal["resume_walkthrough", "behavioral_star"]
SessionStatus = Literal["active", "complete", "abandoned"]


class InterviewState(TypedDict, total=False):
    # --- identity ---
    user_id: str
    session_id: str  # only set on interview_graph runs
    job_id: str  # only set on prep_graph runs
    round_type: RoundType
    n_questions: int

    # --- prep outputs ---
    profile: dict[str, Any] | None
    job: dict[str, Any] | None
    company: dict[str, Any] | None
    prep_done: bool
    force_refresh: bool

    # --- per-turn fields ---
    current_question: dict[str, Any] | None
    current_answer: str | None
    evaluation: dict[str, Any] | None
    turn_index: int
    session_status: SessionStatus

    # --- supervisor routing / observability ---
    next_step: str

    # --- LangGraph chat history (reserved; unused in v1) ---
    messages: list[Any]
