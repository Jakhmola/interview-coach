"""LangGraph node wrappers (Phase 10).

Each function adapts an existing agent node (Phase 6–9) to LangGraph's
``(state) -> state_update`` signature. The Phase 6–9 functions stay
unchanged — these wrappers are thin glue:

* read identity fields off the state,
* call the Phase 6–9 function (or short-circuit on a cache hit),
* push lifecycle / token events through ``get_stream_writer`` so the
  route layer can forward them as SSE,
* return a state delta.

Cache rules (used by the prep graph):

* ``profile_builder`` is skipped iff a ProfileRow exists for the user
  AND its ``source_doc_ids`` match the user's current document list.
  If the user replaced their CV, the doc-id set differs and we re-run.
* ``job_analyzer`` is skipped iff ``jobs.parsed_json`` is non-empty.
* ``company_researcher`` is skipped iff a snapshot row exists for the
  job AND ``state["force_refresh"]`` is False.

Streaming rules (used by the interview graph):

* ``question_generator`` forwards the underlying ``stream_question``
  events as ``token`` / ``done`` writer events, then ``interrupt``s on
  the awaiting-answer signal.
* ``evaluator`` forwards the streaming-JSON ``score`` /
  ``feedback_token`` / ``model_answer_token`` / ``done`` events.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from langgraph.config import get_stream_writer
from langgraph.types import interrupt

from interview_coach.agents.nodes.company_researcher import (
    CompanyNameMissing,
    JobNotAnalyzed,
    NoSearchHits,
    NoUsablePages,
    research_company,
)
from interview_coach.agents.nodes.evaluator import stream_evaluation
from interview_coach.agents.nodes.job_analyzer import JobNotFoundError, analyze_job
from interview_coach.agents.nodes.profile_builder import NoDocumentsError, build_profile
from interview_coach.agents.nodes.question_generator import (
    GenerationPrereqsMissing,
    stream_question,
)
from interview_coach.agents.state import InterviewState
from interview_coach.db import repos
from interview_coach.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)


# --- prep graph nodes -------------------------------------------------


async def node_profile_builder(state: InterviewState) -> dict[str, Any]:
    user_id = uuid.UUID(state["user_id"])
    writer = get_stream_writer()

    async with AsyncSessionLocal() as s:
        existing = await repos.get_profile(s, user_id)
        current_doc_ids: list[str] = []
        if existing is not None:
            docs = await repos.list_documents_for_user(s, user_id)
            current_doc_ids = sorted(str(d.id) for d in docs)
            cached_ids = sorted(str(x) for x in (existing.source_doc_ids or []))

    if existing is not None and current_doc_ids == cached_ids:
        writer({"event": "node_skipped", "node": "profile_builder", "reason": "cached"})
        return {
            "profile": existing.profile_json,
            "next_step": "job_analyzer",
        }

    writer({"event": "node_started", "node": "profile_builder"})
    try:
        profile = await build_profile(user_id)
    except NoDocumentsError as e:
        writer(
            {
                "event": "error",
                "node": "profile_builder",
                "code": "no_documents",
                "detail": str(e),
            }
        )
        raise
    writer({"event": "node_done", "node": "profile_builder"})
    return {
        "profile": profile.model_dump(),
        "next_step": "job_analyzer",
    }


async def node_job_analyzer(state: InterviewState) -> dict[str, Any]:
    user_id = uuid.UUID(state["user_id"])
    job_id = uuid.UUID(state["job_id"])
    writer = get_stream_writer()

    async with AsyncSessionLocal() as s:
        job = await repos.get_job(s, job_id, user_id)

    if job is None:
        writer({"event": "error", "node": "job_analyzer", "code": "job_not_found"})
        raise JobNotFoundError(f"job {job_id} not found")

    if job.parsed_json:
        writer({"event": "node_skipped", "node": "job_analyzer", "reason": "already_analyzed"})
        return {"job": job.parsed_json, "next_step": "company_researcher"}

    writer({"event": "node_started", "node": "job_analyzer"})
    analysis = await analyze_job(job_id, user_id)
    writer({"event": "node_done", "node": "job_analyzer"})
    return {"job": analysis.model_dump(), "next_step": "company_researcher"}


async def node_company_researcher(state: InterviewState) -> dict[str, Any]:
    user_id = uuid.UUID(state["user_id"])
    job_id = uuid.UUID(state["job_id"])
    force_refresh = bool(state.get("force_refresh", False))
    writer = get_stream_writer()

    if not force_refresh:
        async with AsyncSessionLocal() as s:
            existing = await repos.get_company_snapshot_by_job(s, job_id)
        if existing is not None:
            writer({"event": "node_skipped", "node": "company_researcher", "reason": "cached"})
            return {
                "company": existing.snapshot_json,
                "prep_done": True,
                "next_step": "END",
            }

    writer({"event": "node_started", "node": "company_researcher"})
    try:
        snapshot = await research_company(job_id, user_id, force_refresh=force_refresh)
    except (JobNotAnalyzed, CompanyNameMissing, NoSearchHits, NoUsablePages) as e:
        writer(
            {
                "event": "error",
                "node": "company_researcher",
                "code": type(e).__name__,
                "detail": str(e),
            }
        )
        raise
    writer({"event": "node_done", "node": "company_researcher"})
    return {
        "company": snapshot.model_dump(),
        "prep_done": True,
        "next_step": "END",
    }


# --- interview graph nodes -------------------------------------------


async def node_question_generator(state: InterviewState) -> dict[str, Any]:
    """Generate and stream one question; persist the Turn row.

    The interrupt for the user's answer lives in a *separate* downstream
    node (``node_await_answer``). LangGraph 1.x re-executes the
    interrupted node on resume, so doing the LLM streaming and DB write
    here would re-stream and double-write the turn. Splitting the
    interrupt out keeps the side-effecting work behind a clean
    checkpoint boundary.
    """
    session_id = uuid.UUID(state["session_id"])
    user_id = uuid.UUID(state["user_id"])
    writer = get_stream_writer()

    done_payload: dict[str, Any] | None = None
    try:
        async for kind, data in stream_question(session_id=session_id, user_id=user_id):
            if kind == "token":
                writer({"event": "token", "data": data})
            elif kind == "done":
                done_payload = data
                writer({"event": "done", "data": data})
    except GenerationPrereqsMissing as e:
        writer({"event": "error", "code": str(e)})
        raise

    assert done_payload is not None, "stream_question did not emit a done event"

    return {
        "current_question": done_payload,
        "turn_index": done_payload["turn_index"],
        "next_step": "await_answer",
    }


async def node_await_answer(state: InterviewState) -> dict[str, Any]:
    """Single-purpose node that holds the interrupt for the user answer.

    LangGraph re-executes this node on resume; that's fine — its only
    side-effect is calling ``interrupt(...)``.
    """
    current_q = state.get("current_question") or {}
    resume_payload = interrupt({"awaiting": "answer", "turn_id": current_q.get("question_id")})
    answer = (resume_payload or {}).get("answer", "")
    return {"current_answer": answer, "next_step": "evaluator"}


async def node_evaluator(state: InterviewState) -> dict[str, Any]:
    """Stream the evaluation for the latest turn and update state."""
    session_id = uuid.UUID(state["session_id"])
    user_id = uuid.UUID(state["user_id"])
    current_q = state.get("current_question") or {}
    turn_id = uuid.UUID(current_q["question_id"])
    writer = get_stream_writer()

    done_payload: dict[str, Any] | None = None
    async for kind, data in stream_evaluation(
        session_id=session_id, user_id=user_id, turn_id=turn_id
    ):
        if kind == "score":
            writer({"event": "score", "data": data})
        elif kind in ("feedback_token", "model_answer_token"):
            writer({"event": kind, "data": data})
        elif kind in ("feedback_done", "model_answer_done"):
            writer({"event": kind, "data": data})
        elif kind == "model_answer_error":
            writer({"event": kind, "data": data})
        elif kind == "done":
            done_payload = data
            writer({"event": "done", "data": data})

    assert done_payload is not None, "stream_evaluation did not emit a done event"

    return {
        "evaluation": done_payload,
        "session_status": done_payload["session_status"],
        "next_step": "END",
    }
