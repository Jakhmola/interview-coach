"""LangGraph supervisor graphs (Phase 10, extended Phase 21.1).

Two graphs, not one — see `plan/master.md` Phase 10 and the design
notes in `plan/current-phase.md`:

* ``prep_graph`` (Phase 21.1) — ``profile_builder → doc_mapping_loop
  → job_analyzer → company_researcher → END`` where ``doc_mapping_loop``
  is three nodes (prepare_mapping_suggestion → await_mapping_confirm
  → apply_or_skip_mapping) that loop back until every unmapped
  project_doc has been confirmed or skipped. Checkpointed by the
  same ``AsyncSqliteSaver`` used by the interview graph,
  ``thread_id = "prep:{user_id}:{job_id}"`` — a mid-prep api crash
  resumes from the last completed node on the next ``/prepare`` POST.
* ``interview_graph`` (Phase 34) — a real loop owning the whole session:
  ``interviewer → await_candidate (interrupt) → [probe/clarify/nudge →
  interviewer | advance → evaluator] → evaluator → [topics remain →
  interviewer | END]``. Checkpointed by AsyncSqliteSaver,
  ``thread_id = "interview:{session_id}"`` — one paused run per session,
  spanning many ``/message`` round-trips. Survives api restarts.

The streaming model: nodes write opaque event dicts via
``get_stream_writer()``; the route consumes
``graph.astream(..., stream_mode="custom")`` and translates those dicts
into SSE on the wire. This keeps the SSE bytes byte-identical to
Phase 8/9 even though the orchestration moved to LangGraph.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph

from interview_coach.agents.graph_nodes import (
    node_apply_or_skip_mapping,
    node_await_candidate,
    node_await_mapping_confirm,
    node_company_researcher,
    node_evaluator,
    node_interviewer,
    node_job_analyzer,
    node_prepare_mapping_suggestion,
    node_profile_builder,
)
from interview_coach.agents.nodes.github_ingest import (
    node_await_repo_selection,
    node_github_discover,
    node_github_ingest_and_fold,
)
from interview_coach.agents.state import InterviewState


def build_prep_graph(checkpointer: BaseCheckpointSaver | None) -> Any:
    """Compile the prep graph with the given checkpointer.

    Topology:

        START
          → profile_builder
          → github_discover ─┐ (next_step: prompt / fold-only / skip)
              ├── await_repo_selection → github_ingest_and_fold ┐
              └── github_ingest_and_fold ──────────────────────┤
                                                                ↓
          → prepare_mapping_suggestion ─┐
              ↑                         │ (when no more unmapped docs)
              │                         ↓
              ├── await_mapping_confirm
              │       ↓
              └── apply_or_skip_mapping
                              │
                              ↓ (cond: more unmapped → loop, else → job)
          → job_analyzer
          → company_researcher
          → END

    Phase 21.1 swapped the parallel ``doc_intake_fanout`` for a strict
    one-at-a-time HITL loop. Each unmapped project_doc surfaces a
    ``mapping_suggestion`` SSE event, halts on an interrupt for user
    confirmation, then either persists via ``apply_mapping`` or marks
    the doc as skipped and continues.

    Checkpointing isn't optional here — ``interrupt(...)`` requires it.
    """
    g: StateGraph = StateGraph(InterviewState)
    g.add_node("profile_builder", node_profile_builder)
    g.add_node("github_discover", node_github_discover)
    g.add_node("await_repo_selection", node_await_repo_selection)
    g.add_node("github_ingest_and_fold", node_github_ingest_and_fold)
    g.add_node("prepare_mapping_suggestion", node_prepare_mapping_suggestion)
    g.add_node("await_mapping_confirm", node_await_mapping_confirm)
    g.add_node("apply_or_skip_mapping", node_apply_or_skip_mapping)
    g.add_node("job_analyzer", node_job_analyzer)
    g.add_node("company_researcher", node_company_researcher)

    g.add_edge(START, "profile_builder")
    # Phase 32: github segment runs after profile_builder, before the
    # doc-mapping loop. ``github_discover`` routes three ways via next_step:
    # prompt (await_repo_selection), re-fold only (github_ingest_and_fold), or
    # skip the segment entirely (prepare_mapping_suggestion).
    g.add_edge("profile_builder", "github_discover")
    g.add_conditional_edges(
        "github_discover",
        lambda s: s.get("next_step") or "prepare_mapping_suggestion",
        {
            "await_repo_selection": "await_repo_selection",
            "github_ingest_and_fold": "github_ingest_and_fold",
            "prepare_mapping_suggestion": "prepare_mapping_suggestion",
        },
    )
    g.add_edge("await_repo_selection", "github_ingest_and_fold")
    # Phase 32 follow-up 3: ingest_and_fold normally advances to the mapping
    # loop, but on an unresolved repo-ingest failure it routes *back* to
    # ``await_repo_selection`` (the prep⊥interview barrier) so the user retries
    # or deselects the broken repos before prep can finalize.
    g.add_conditional_edges(
        "github_ingest_and_fold",
        lambda s: s.get("next_step") or "prepare_mapping_suggestion",
        {
            "await_repo_selection": "await_repo_selection",
            "prepare_mapping_suggestion": "prepare_mapping_suggestion",
        },
    )
    # prepare_mapping_suggestion returns next_step ∈
    #   {"job_analyzer", "await_mapping_confirm", "apply_or_skip_mapping"}
    g.add_conditional_edges(
        "prepare_mapping_suggestion",
        lambda s: s.get("next_step") or "await_mapping_confirm",
        {
            "await_mapping_confirm": "await_mapping_confirm",
            "apply_or_skip_mapping": "apply_or_skip_mapping",
            "job_analyzer": "job_analyzer",
        },
    )
    g.add_edge("await_mapping_confirm", "apply_or_skip_mapping")
    g.add_edge("apply_or_skip_mapping", "prepare_mapping_suggestion")
    g.add_edge("job_analyzer", "company_researcher")
    g.add_edge("company_researcher", END)
    return g.compile(checkpointer=checkpointer) if checkpointer is not None else g.compile()


def build_interview_graph(checkpointer: BaseCheckpointSaver | None) -> Any:
    """Compile the interview graph with the given checkpointer (Phase 34).

    The graph owns the whole session loop — a single logical run that spans
    many ``/message`` HTTP round-trips via ``interrupt`` / ``Command(resume=…)``
    on a session-keyed checkpoint thread (``interview:{session_id}``), exactly
    as ``prep_graph`` spans ``/prepare`` + ``/prepare/resume`` (ADR 0004).

    Topology::

        START
          → interviewer ─┐ (next_move)
              ├── advance → evaluator
              └── question/probe/clarify/nudge → await_candidate (interrupt)
                                                        ↓
                                                   interviewer  (loop)
          evaluator ─┐ (session_status)
              ├── active   → interviewer   (open the next topic)
              └── complete → END           (wrap)

    The ``interviewer`` node decides the move; the budget/edge disposes:
    ``advance`` routes to the evaluator (close + evaluate the thread); any
    other move routes to ``await_candidate`` (pause for the candidate). After a
    thread closes, ``active`` loops back to open the next topic; ``complete``
    (topic budget spent) ends the run. Checkpointing isn't optional —
    ``interrupt(...)`` requires it.
    """
    g: StateGraph = StateGraph(InterviewState)
    g.add_node("interviewer", node_interviewer)
    g.add_node("await_candidate", node_await_candidate)
    g.add_node("evaluator", node_evaluator)

    g.add_edge(START, "interviewer")
    g.add_conditional_edges(
        "interviewer",
        lambda s: "evaluator" if s.get("next_move") == "advance" else "await_candidate",
        {"evaluator": "evaluator", "await_candidate": "await_candidate"},
    )
    g.add_edge("await_candidate", "interviewer")
    g.add_conditional_edges(
        "evaluator",
        lambda s: "end" if s.get("session_status") == "complete" else "interviewer",
        {"interviewer": "interviewer", "end": END},
    )
    return g.compile(checkpointer=checkpointer) if checkpointer is not None else g.compile()


@asynccontextmanager
async def open_checkpointer(graph_db_path: str) -> AsyncIterator[BaseCheckpointSaver]:
    """Yield an open AsyncSqliteSaver. Caller is the api lifespan.

    For ``:memory:`` (in tests / quick scripts) the SQLite db is
    process-local and disappears at shutdown. For a real path, the
    parent directory is created if missing.
    """
    if graph_db_path != ":memory:":
        Path(graph_db_path).parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(graph_db_path) as saver:
        yield saver
