"""Sessions + interview streaming endpoints (Phase 8/9, rewritten Phase 10).

Phase 10 routes the per-session interview lifecycle through a LangGraph
``StateGraph`` (compiled once at lifespan startup, stashed on
``app.state``). The on-the-wire SSE format is unchanged from Phase 9 —
the route is a thin translator from the graph's custom-stream writer
events to SSE events.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession

from interview_coach.agents.nodes.company_researcher import (
    CompanyNameMissing,
    NoSearchHits,
    NoUsablePages,
)
from interview_coach.agents.nodes.evaluator import (
    TurnNotAnswered,
    TurnNotFound,
)
from interview_coach.agents.nodes.profile_builder import NoDocumentsError
from interview_coach.agents.nodes.question_generator import GenerationPrereqsMissing
from interview_coach.agents.streaming_json import StreamingJsonError
from interview_coach.api.auth.deps import get_current_user
from interview_coach.api.sessions.schemas import (
    AnswerSubmitRequest,
    PrepareMappingResumeRequest,
    PrepareRequest,
    PrepStatusOut,
    RoundType,
    SessionCreateRequest,
    SessionDetail,
    SessionOut,
    SessionStatus,
    TurnOut,
)
from interview_coach.api.streaming import SSE_HEADERS, sse_event
from interview_coach.db import repos
from interview_coach.db.models import User
from interview_coach.db.session import get_db
from interview_coach.observability.langfuse import (
    flush_langfuse,
    langfuse_callback,
    trace_attributes,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: SessionCreateRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> SessionOut:
    job = await repos.get_job(session, body.job_id, user.id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job_not_found")
    if not job.parsed_json:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "job_not_analyzed")

    profile = await repos.get_profile(session, user.id)
    if profile is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "profile_missing")

    snapshot = await repos.get_company_snapshot_by_job(session, body.job_id)
    if snapshot is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "company_snapshot_missing")

    row = await repos.create_session(
        session,
        user_id=user.id,
        job_id=body.job_id,
        round_type=body.round_type.value,
        n_questions=body.n_questions,
    )
    return SessionOut.model_validate(row)


@router.get("", response_model=list[SessionOut])
async def list_sessions(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[SessionOut]:
    rows = await repos.list_sessions_for_user(session, user.id)
    return [SessionOut.model_validate(r) for r in rows]


@router.get("/{session_id}", response_model=SessionDetail)
async def get_session_detail(
    session_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> SessionDetail:
    row = await repos.get_session(session, session_id, user.id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session_not_found")
    turns = await repos.list_turns_for_session(session, session_id)
    return SessionDetail(
        id=row.id,
        user_id=row.user_id,
        job_id=row.job_id,
        round_type=RoundType(row.round_type),
        status=SessionStatus(row.status),
        n_questions=row.n_questions,
        created_at=row.created_at,
        turns=[TurnOut.model_validate(t) for t in turns],
    )


@router.post("/{session_id}/abandon", response_model=SessionOut)
async def abandon_session(
    session_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> SessionOut:
    row = await repos.get_session(session, session_id, user.id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session_not_found")
    if row.status == "active":
        await repos.update_session_status(session, session_id, user.id, "abandoned")
        row = await repos.get_session(session, session_id, user.id)
        assert row is not None
    return SessionOut.model_validate(row)


# --- Phase 10: prepare endpoint -------------------------------------


@router.get("/prepare/status", response_model=PrepStatusOut)
async def prepare_status(
    job_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
    detail: bool = False,
) -> PrepStatusOut:
    """Read-only readiness view for the frontend setup flow.

    Phase 21: default response is readiness booleans only. Pass
    ``?detail=true`` to include the full profile / job / company
    payloads (callers should pass that flag explicitly — SetupPage's
    2-4 s poll loop doesn't need them).
    """
    job = await repos.get_job(session, job_id, user.id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job_not_found")

    docs = await repos.list_documents_for_user(session, user.id)
    has_cv = any(d.kind == "cv" for d in docs)
    profile = await repos.get_profile(session, user.id)
    snapshot = await repos.get_company_snapshot_by_job(session, job_id)
    unmapped = await repos.list_unmapped_project_docs_for_user(session, user.id)

    profile_ready = profile is not None
    job_analyzed = bool(job.parsed_json)
    company_researched = snapshot is not None

    missing: list[str] = []
    if not has_cv:
        missing.append("cv")
    if not profile_ready:
        missing.append("profile")
    if not job_analyzed:
        missing.append("job_analysis")
    if not company_researched:
        missing.append("company_research")

    return PrepStatusOut(
        job_id=job.id,
        has_cv=has_cv,
        profile_ready=profile_ready,
        job_analyzed=job_analyzed,
        company_researched=company_researched,
        can_start=not missing,
        missing=missing,
        unmapped_project_doc_count=len(unmapped),
        profile=(profile.profile_json if (detail and profile is not None) else None),
        job=(job.parsed_json if detail else None),
        company=(
            {
                "company_name": snapshot.company_name,
                "snapshot": snapshot.snapshot_json,
                "source_urls": snapshot.source_urls,
                "updated_at": snapshot.updated_at,
            }
            if (detail and snapshot is not None)
            else None
        ),
    )


PREP_FORWARDED_EVENTS = frozenset(
    {
        "node_started",
        "node_done",
        "node_skipped",
        "mapping_suggestion",
        "mapping_suggestion_failed",
        "mapping_applied",
        "mapping_skipped",
        "mapping_apply_failed",
    }
)


def _prep_event_stream(
    *,
    prep_graph: Any,
    graph_input: Any,
    prep_config: dict[str, Any],
    user_id: uuid.UUID,
    job_id: uuid.UUID,
    trace_meta: dict[str, Any],
) -> AsyncIterator[bytes]:
    """Shared SSE pump for the prep_graph. Used by both the fresh
    ``/prepare`` POST and the ``/prepare/resume`` POST that the FE sends
    after the user confirms / skips a mapping suggestion.

    The stream ends in one of three states:
    * ``done``  — prep_graph reached END.
    * ``awaiting_mapping`` — prep_graph hit a mapping interrupt; the FE
      already received the ``mapping_suggestion`` event for the doc.
    * ``error`` — a node-level failure surfaced before END.
    """

    async def gen() -> AsyncIterator[bytes]:
        saw_mapping_interrupt = False
        try:
            with trace_attributes(
                user_id=str(user_id),
                metadata=trace_meta,
                tags=["graph:prep"],
            ):
                async for chunk in prep_graph.astream(
                    graph_input, config=prep_config, stream_mode="custom"
                ):
                    if chunk.get("event") == "mapping_suggestion":
                        saw_mapping_interrupt = True
                    event = chunk.get("event")
                    if event in PREP_FORWARDED_EVENTS:
                        yield sse_event(event, {k: v for k, v in chunk.items() if k != "event"})
                    elif event == "error":
                        yield sse_event("error", {k: v for k, v in chunk.items() if k != "event"})
                        return
                # astream exit: either the graph reached END or it paused
                # at a mapping interrupt. The interrupt path ends the
                # generator without emitting a langgraph-internal frame
                # because we run stream_mode="custom".
                if saw_mapping_interrupt:
                    yield sse_event(
                        "awaiting_mapping",
                        {"job_id": str(job_id)},
                    )
                else:
                    yield sse_event("done", {"job_id": str(job_id), "ready": True})
        except (NoDocumentsError, NoSearchHits, NoUsablePages, CompanyNameMissing) as e:
            yield sse_event("error", {"code": type(e).__name__, "detail": str(e)})
        finally:
            await flush_langfuse()

    return gen()


@router.post("/prepare")
async def prepare_session(
    body: PrepareRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
    """Start (or restart from scratch) the prep_graph run for a job.

    SSE stream of node lifecycle + mapping events. The graph pauses on
    each unmapped project_doc — the FE then POSTs to ``/prepare/resume``
    with the user's confirmation to advance. Node-level errors come back
    as ``event: error`` mid-stream; pre-stream input errors come back as
    HTTP 4xx.
    """
    job = await repos.get_job(session, body.job_id, user.id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job_not_found")
    docs = await repos.list_documents_for_user(session, user.id)
    if not docs:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no_documents")

    prep_graph = request.app.state.prep_graph
    # Phase 22 fix: a fresh /prepare POST must start the graph from
    # START, not pick up from a prior END checkpoint. LangGraph
    # short-circuits ``astream(input, config=thread_at_end)`` when the
    # input is structurally the same as the prior run, so running prep
    # twice on the same job — exactly what the work-driven auto-prep
    # does after a project_doc upload — silently no-ops without this
    # reset. The resume path (POST /prepare/resume) deliberately keeps
    # the thread so the interrupt() handshake survives the round-trip.
    checkpointer = getattr(request.app.state, "checkpointer", None)
    if checkpointer is not None:
        thread_id = f"prep:{user.id}:{body.job_id}"
        try:
            await checkpointer.adelete_thread(thread_id)
        except Exception:  # noqa: BLE001
            logger.exception("prep thread reset failed for %s", thread_id)

    initial_state: dict[str, Any] = {
        "user_id": str(user.id),
        "job_id": str(body.job_id),
        "force_refresh": body.force_refresh,
        # Phase 21.1: each fresh /prepare run starts with an empty
        # skiplist; the user gets a fresh chance to confirm any
        # previously-skipped project_doc.
        "skipped_mapping_doc_ids": [],
        "pending_mapping": None,
        "mapping_resume": None,
    }
    prep_config = _with_callbacks(_thread_config_for_prep(user.id, body.job_id))
    trace_meta = {
        "graph": "prep",
        "user_id": str(user.id),
        "job_id": str(body.job_id),
        "force_refresh": str(body.force_refresh),
    }
    stream = _prep_event_stream(
        prep_graph=prep_graph,
        graph_input=initial_state,
        prep_config=prep_config,
        user_id=user.id,
        job_id=body.job_id,
        trace_meta=trace_meta,
    )
    return StreamingResponse(stream, media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/prepare/resume")
async def prepare_session_resume(
    body: PrepareMappingResumeRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> StreamingResponse:
    """Resume an interrupted prep_graph after the user confirms or skips
    a mapping suggestion. The body carries the user's decision; LangGraph
    threads it into the ``await_mapping_confirm`` interrupt and the graph
    advances to the next unmapped doc (or to ``job_analyzer`` if none).
    """
    prep_graph = request.app.state.prep_graph
    prep_config = _with_callbacks(_thread_config_for_prep(user.id, body.job_id))
    trace_meta = {
        "graph": "prep",
        "phase": "resume_mapping",
        "user_id": str(user.id),
        "job_id": str(body.job_id),
    }
    resume_payload = {
        "action": body.action,
        "rows": [r.model_dump() for r in body.rows] if body.action == "apply" else [],
        "title": body.title,
        "extracted": body.extracted.model_dump() if body.extracted is not None else None,
    }
    stream = _prep_event_stream(
        prep_graph=prep_graph,
        graph_input=Command(resume=resume_payload),
        prep_config=prep_config,
        user_id=user.id,
        job_id=body.job_id,
        trace_meta=trace_meta,
    )
    return StreamingResponse(stream, media_type="text/event-stream", headers=SSE_HEADERS)


# --- Phase 8/9 routes, rewritten to drive interview_graph -----------


def _thread_config(session_id: uuid.UUID, turn_index: int) -> dict[str, Any]:
    """One graph thread per (session, turn_index).

    Each turn is its own pipeline (question_generator → interrupt →
    evaluator → END). Per-turn thread_ids let a session walk forward
    cleanly without colliding with prior turn checkpoints.
    """
    return {"configurable": {"thread_id": f"{session_id}:turn_{turn_index}"}}


def _thread_config_for_prep(user_id: uuid.UUID, job_id: uuid.UUID) -> dict[str, Any]:
    """Phase 21: per-(user, job) prep_graph thread.

    Mid-prep crash + resume reads the last completed checkpoint from
    this thread. Distinct ``prep:`` prefix keeps the namespace clear of
    interview-graph threads sharing the same AsyncSqliteSaver.
    """
    return {"configurable": {"thread_id": f"prep:{user_id}:{job_id}"}}


def _with_callbacks(config: dict[str, Any]) -> dict[str, Any]:
    """Attach the Langfuse callback to a graph config when tracing is enabled.

    No-op when Langfuse env is unset. Mutates a copy — never the input.
    Trace-level attributes (user_id, session_id, metadata, tags) are
    applied by the surrounding ``trace_attributes`` context manager.
    """
    cb = langfuse_callback()
    if cb is None:
        return config
    new = dict(config)
    new["callbacks"] = [*new.get("callbacks", []), cb]
    return new


async def _hydrate_interview_context(
    session: AsyncSession, *, user_id: uuid.UUID, job_id: uuid.UUID
) -> dict[str, Any | None]:
    """Phase 20: pre-load profile / job analysis / company snapshot once per
    request so the graph nodes can skip per-turn DB round-trips.

    All three are gathered concurrently against the same session. Missing
    rows surface as ``None`` — the graph nodes still raise
    ``GenerationPrereqsMissing`` if a required value is absent, so this
    helper stays liberal.
    """
    profile_row, job_row, snap_row = await asyncio.gather(
        repos.get_profile(session, user_id),
        repos.get_job(session, job_id, user_id),
        repos.get_company_snapshot_by_job(session, job_id),
    )
    return {
        "profile": profile_row.profile_json if profile_row is not None else None,
        "job": job_row.parsed_json if job_row is not None else None,
        "company": snap_row.snapshot_json if snap_row is not None else None,
    }


@router.post("/{session_id}/next_question")
async def next_question(
    session_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
    """SSE stream of the next question's tokens.

    Drives ``interview_graph`` until it interrupts on the answer gate.
    """
    row = await repos.get_session(session, session_id, user.id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session_not_found")
    if row.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, f"session_status_{row.status}")

    turns = await repos.list_turns_for_session(session, session_id)
    if turns and turns[-1].answer is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "previous_turn_unanswered")
    if len(turns) >= row.n_questions:
        raise HTTPException(status.HTTP_409_CONFLICT, "session_complete")

    turn_index = len(turns)
    hydrated = await _hydrate_interview_context(session, user_id=user.id, job_id=row.job_id)
    interview_graph = request.app.state.interview_graph
    config = _with_callbacks(_thread_config(session_id, turn_index))
    initial_state: dict[str, Any] = {
        "user_id": str(user.id),
        "session_id": str(session_id),
        "round_type": row.round_type,
        "n_questions": row.n_questions,
        "turn_index": turn_index,
        "profile": hydrated["profile"],
        "job": hydrated["job"],
        "company": hydrated["company"],
    }
    trace_meta = {
        "graph": "interview",
        "phase": "next_question",
        "user_id": str(user.id),
        "session_id": str(session_id),
        "round_type": row.round_type,
        # Langfuse v4 propagated-attribute values must be strings.
        "turn_index": str(turn_index),
    }
    trace_tags = ["graph:interview", f"round:{row.round_type}", "phase:next_question"]

    async def event_stream() -> AsyncIterator[bytes]:
        try:
            with trace_attributes(
                user_id=str(user.id),
                session_id=str(session_id),
                metadata=trace_meta,
                tags=trace_tags,
            ):
                async for chunk in interview_graph.astream(
                    initial_state, config=config, stream_mode="custom"
                ):
                    event = chunk.get("event")
                    if event == "token":
                        yield sse_event("token", chunk.get("data", ""))
                    elif event == "done":
                        yield sse_event("done", chunk.get("data", {}))
                    elif event == "error":
                        yield sse_event("error", {k: v for k, v in chunk.items() if k != "event"})
                        return
        except GenerationPrereqsMissing as e:
            logger.warning("Generation prereqs missing for session=%s: %s", session_id, e)
            yield sse_event("error", {"code": str(e)})
        except StreamingJsonError as e:
            logger.exception("Streaming JSON failed for session=%s", session_id)
            yield sse_event("error", {"code": "streaming_json_error", "detail": str(e)})
        finally:
            await flush_langfuse()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.post("/{session_id}/answer")
async def submit_answer(
    session_id: uuid.UUID,
    body: AnswerSubmitRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
    """Save the answer, resume the graph, and stream the evaluator output."""
    answer = body.answer.strip()
    if not answer:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty_answer")

    sess = await repos.get_session(session, session_id, user.id)
    if sess is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session_not_found")
    if sess.status != "active":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"session_status_{sess.status}")

    turns = await repos.list_turns_for_session(session, session_id)
    if not turns:
        raise HTTPException(status.HTTP_409_CONFLICT, "no_active_turn")
    latest = turns[-1]
    if latest.answer is not None and latest.score is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "turn_already_evaluated")

    if latest.answer is None:
        await repos.update_turn_answer(session, latest.id, answer)

    interview_graph = request.app.state.interview_graph
    config = _with_callbacks(_thread_config(session_id, latest.turn_index))
    trace_meta = {
        "graph": "interview",
        "phase": "submit_answer",
        "user_id": str(user.id),
        "session_id": str(session_id),
        "round_type": sess.round_type,
        # Langfuse v4 propagated-attribute values must be strings.
        "turn_index": str(latest.turn_index),
    }
    trace_tags = ["graph:interview", f"round:{sess.round_type}", "phase:submit_answer"]

    async def event_stream() -> AsyncIterator[bytes]:
        try:
            with trace_attributes(
                user_id=str(user.id),
                session_id=str(session_id),
                metadata=trace_meta,
                tags=trace_tags,
            ):
                async for chunk in interview_graph.astream(
                    Command(resume={"answer": answer}),
                    config=config,
                    stream_mode="custom",
                ):
                    event = chunk.get("event")
                    if event == "score":
                        score_data = chunk.get("data")
                        payload = (
                            score_data if isinstance(score_data, dict) else {"score": score_data}
                        )
                        yield sse_event("score", payload)
                    elif event in ("feedback_token", "model_answer_token"):
                        yield sse_event(event, chunk.get("data", ""))
                    elif event in ("feedback_done", "model_answer_done"):
                        yield sse_event(event, {})
                    elif event == "model_answer_error":
                        yield sse_event(event, chunk.get("data", {}))
                    elif event == "done":
                        yield sse_event("done", chunk.get("data", {}))
                    elif event == "error":
                        yield sse_event("error", {k: v for k, v in chunk.items() if k != "event"})
                        return
        except (TurnNotFound, TurnNotAnswered) as e:
            yield sse_event("error", {"code": type(e).__name__, "detail": str(e)})
        except StreamingJsonError as e:
            logger.exception("Evaluator streaming failed for session=%s", session_id)
            yield sse_event("error", {"code": "streaming_json_error", "detail": str(e)})
        finally:
            await flush_langfuse()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
