"""Shared TypedDict state for the LangGraph supervisor (Phase 10).

Two compiled graphs share this same state:

* ``prep_graph`` — runs ProfileBuilder → JobAnalyzer → CompanyResearcher.
  Reads ``user_id``, ``job_id``, ``force_refresh``; sets ``profile``,
  ``job``, ``company``, ``prep_done``.
* ``interview_graph`` — runs QuestionGenerator → (interrupt) → Evaluator,
  looping until the session completes. Reads ``user_id``, ``session_id``,
  ``round_type``, ``n_questions``; mutates ``current_question``,
  ``current_answer``, ``evaluation``, ``turn_index``, ``session_status``.

``next_step`` is read by exactly one edge: the conditional edge out of
``prepare_mapping_suggestion`` that drives the doc-mapping HITL loop (loop
back to handle the next unmapped doc vs. advance to ``job_analyzer``). Every
other edge in both graphs is static, so only that node sets ``next_step``.
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

    # --- prep outputs (also hydrated into interview_graph initial_state
    # by api/sessions/routes.py so question_generator and evaluator can
    # skip per-turn DB reads — Phase 20). ---
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

    # --- prep_graph doc-mapping loop (Phase 21.1) ---
    # ``pending_mapping``: the intake-result the prepare node stashes for
    # the await + apply nodes downstream. Persisted on state so a resume
    # replay doesn't re-run the LLM (which would also produce a different
    # suggestion than the one the user confirmed).
    pending_mapping: dict[str, Any] | None
    # ``mapping_resume``: the user's resume payload from the most recent
    # interrupt — read by apply_or_skip, then cleared.
    mapping_resume: dict[str, Any] | None
    # ``skipped_mapping_doc_ids``: doc ids the user explicitly skipped
    # during the *current* prep run. Scoped to the prep run via the
    # ``prep:{user}:{job}`` thread; cleared by ``initial_state`` on each
    # fresh ``/prepare`` POST so a returning user can re-decide.
    skipped_mapping_doc_ids: list[str]

    # --- doc-mapping loop routing ---
    # Read only by prepare_mapping_suggestion's conditional edge (loop vs.
    # advance to job_analyzer); all other edges are static. Set only by that
    # node.
    next_step: str

    # --- LangGraph chat history (reserved; unused in v1) ---
    messages: list[Any]
