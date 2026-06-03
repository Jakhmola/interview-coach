"""Shared TypedDict state for the LangGraph supervisor (Phase 10, 34).

Two compiled graphs share this same state:

* ``prep_graph`` — runs ProfileBuilder → JobAnalyzer → CompanyResearcher.
  Reads ``user_id``, ``job_id``, ``force_refresh``; sets ``profile``,
  ``job``, ``company``, ``prep_done``.
* ``interview_graph`` (Phase 34) — a real loop:
  ``interviewer → await_candidate (interrupt) → [probe/clarify/nudge →
  interviewer | advance → evaluator] → evaluator → [topics remain →
  interviewer | END]``. Checkpointed per **session** (one paused run per
  session), so a single logical run spans many ``/message`` HTTP round-trips
  — exactly like ``prep_graph`` spans ``/prepare`` + ``/prepare/resume``.
  Reads ``user_id``, ``session_id``, ``round_type``, ``n_questions``;
  carries the open thread (``thread_id`` / ``thread_index`` / ``anchors`` /
  ``focus_*`` / ``grounding`` / ``followups_used``), the candidate's latest
  ``pending_message``, the conductor's ``next_move``, and ``session_status``.

``next_step`` is read by exactly one edge: the conditional edge out of
``prepare_mapping_suggestion`` that drives the doc-mapping HITL loop (loop
back to handle the next unmapped doc vs. advance to ``job_analyzer``). The
interview loop routes on ``next_move`` / ``session_status`` instead. Every
other edge in both graphs is static.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

RoundType = Literal["experience_deep_dive", "technical_challenge", "behavioral_star"]
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

    # --- interview thread loop (Phase 34) ---
    # The open thread the interviewer is conducting. ``thread_id`` is None
    # between threads (after a thread closes, before the next opens); the
    # interviewer node reads that to decide open-thread vs. conduct.
    thread_id: str | None
    thread_index: int
    # The thread's fixed agenda + focus, set at thread-open. Probes target
    # ``anchors``; they don't mint new ones. ``grounding`` is the repo passages
    # retrieved ONCE at thread-open (experience round), reused for every
    # conductor step and the thread-close model answer.
    anchors: list[str]
    focus_key: str | None
    focus_label: str | None
    focus_document_ids: list[str]
    grounding: list[dict[str, Any]]
    # ``followups_used``: combined probe+clarify+nudge count for the open
    # thread — the hard cap (``RoundStrategy.max_followups``) the budget guard
    # enforces so a thread always terminates.
    followups_used: int
    # ``pending_message``: the candidate's latest answer, stashed by the pure
    # ``await_candidate`` interrupt node and consumed (persisted as a candidate
    # Message) by the interviewer node on its next run. None between answers.
    pending_message: str | None
    # ``next_move``: the conductor's chosen move (``question`` / ``probe`` /
    # ``clarify`` / ``nudge`` / ``advance``) — the conditional edge out of the
    # interviewer node routes on it (advance → evaluator, else → await).
    next_move: str | None
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

    # --- prep_graph github segment (Phase 32) ---
    # ``github_repos``: the repo listing the discover node fetched, stashed so
    # the ingest node reads metadata (description, branch, url) without a second
    # list call — and so a resume replay doesn't re-list.
    github_repos: list[dict[str, Any]] | None
    # ``github_resume``: the user's repo-selection resume payload
    # (``{"selected_urls": [...]}``), set by the await node, read by ingest.
    github_resume: dict[str, Any] | None
    # ``github_failures`` (Phase 32 follow-up 3): per-repo ingest failures from
    # the last ingest pass — ``[{html_url, full_name, step, code, reason}]``.
    # Non-empty → the ingest node routes back to ``await_repo_selection`` (the
    # prep⊥interview barrier) instead of finalizing; cleared on a clean run.
    github_failures: list[dict[str, Any]]

    # --- conditional-edge routing ---
    # Read by the conditional edges out of ``prepare_mapping_suggestion`` (loop
    # vs. advance) AND out of ``github_discover`` (prompt vs. fold-only vs. skip
    # segment). Each setting node's value is consumed by the edge immediately
    # after it, and the two nodes run sequentially, so they never collide.
    next_step: str

    # --- LangGraph chat history (reserved; unused in v1) ---
    messages: list[Any]
