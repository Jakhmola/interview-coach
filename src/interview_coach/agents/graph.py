"""LangGraph supervisor graphs (Phase 10).

Two graphs, not one — see `plan/master.md` Phase 10 and the design
notes in `plan/current-phase.md`:

* ``prep_graph`` — ``profile_builder → job_analyzer → company_researcher → END``.
  No checkpointer (each node is idempotent against Postgres).
* ``interview_graph`` — ``question_generator → (interrupt) → evaluator → loop``.
  Checkpointed by AsyncSqliteSaver, ``thread_id = str(session_id)``.
  Survives api restarts.

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
    node_await_answer,
    node_company_researcher,
    node_evaluator,
    node_job_analyzer,
    node_profile_builder,
    node_question_generator,
)
from interview_coach.agents.state import InterviewState


def build_prep_graph() -> Any:
    """Compile the prep graph (no checkpointer).

    Linear pipeline: profile_builder → job_analyzer → company_researcher.
    Each node short-circuits on cache.
    """
    g: StateGraph = StateGraph(InterviewState)
    g.add_node("profile_builder", node_profile_builder)
    g.add_node("job_analyzer", node_job_analyzer)
    g.add_node("company_researcher", node_company_researcher)

    g.add_edge(START, "profile_builder")
    g.add_edge("profile_builder", "job_analyzer")
    g.add_edge("job_analyzer", "company_researcher")
    g.add_edge("company_researcher", END)
    return g.compile()


def build_interview_graph(checkpointer: BaseCheckpointSaver | None) -> Any:
    """Compile the interview graph with the given checkpointer.

    Each *turn* of an n-question session is one graph run:
    ``question_generator → (interrupt: await answer) → evaluator → END``.

    The session-level loop ("ask another question") happens at the route
    layer — a new ``next_question`` request invokes the graph again on
    the same ``thread_id``. Two reasons this beats a graph-level loop:

    1. The user controls pacing via the UI ("Next question" button),
       not the graph. Looping inside the graph would have the next
       question streaming in immediately after the evaluator finishes.
    2. Resumability is simpler: a single linear pipeline with one
       interrupt point. Any session can be in exactly one of three
       states — fresh, awaiting-answer, or between-turns.
    """
    g: StateGraph = StateGraph(InterviewState)
    g.add_node("question_generator", node_question_generator)
    g.add_node("await_answer", node_await_answer)
    g.add_node("evaluator", node_evaluator)

    g.add_edge(START, "question_generator")
    g.add_edge("question_generator", "await_answer")
    g.add_edge("await_answer", "evaluator")
    g.add_edge("evaluator", END)
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
