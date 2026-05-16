"""Sessions + interview streaming endpoints (Phase 8/9, rewritten Phase 10).

Phase 10 routes the per-session interview lifecycle through a LangGraph
``StateGraph`` (compiled once at lifespan startup, stashed on
``app.state``). The on-the-wire SSE format is unchanged from Phase 9 —
the route is a thin translator from the graph's custom-stream writer
events to SSE events.
"""

from __future__ import annotations

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
) -> PrepStatusOut:
    """Read-only readiness view for the frontend setup flow."""
    job = await repos.get_job(session, job_id, user.id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job_not_found")

    docs = await repos.list_documents_for_user(session, user.id)
    has_cv = any(d.kind == "cv" for d in docs)
    profile = await repos.get_profile(session, user.id)
    snapshot = await repos.get_company_snapshot_by_job(session, job_id)

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
        profile=profile.profile_json if profile is not None else None,
        job=job.parsed_json,
        company=(
            {
                "company_name": snapshot.company_name,
                "snapshot": snapshot.snapshot_json,
                "source_urls": snapshot.source_urls,
                "updated_at": snapshot.updated_at,
            }
            if snapshot is not None
            else None
        ),
    )


@router.post("/prepare")
async def prepare_session(
    body: PrepareRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
    """Run profile_builder → job_analyzer → company_researcher.

    SSE stream of node lifecycle events. Node-level errors come back as
    ``event: error`` mid-stream; pre-stream input errors come back as
    HTTP 4xx.
    """
    job = await repos.get_job(session, body.job_id, user.id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job_not_found")
    docs = await repos.list_documents_for_user(session, user.id)
    if not docs:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no_documents")

    prep_graph = request.app.state.prep_graph
    initial_state: dict[str, Any] = {
        "user_id": str(user.id),
        "job_id": str(body.job_id),
        "force_refresh": body.force_refresh,
    }
    prep_config = _with_callbacks({})
    trace_meta = {
        "graph": "prep",
        "user_id": str(user.id),
        "job_id": str(body.job_id),
        # Langfuse v4 propagated-attribute values must be strings.
        "force_refresh": str(body.force_refresh),
    }

    async def event_stream() -> AsyncIterator[bytes]:
        try:
            with trace_attributes(
                user_id=str(user.id),
                metadata=trace_meta,
                tags=["graph:prep"],
            ):
                async for chunk in prep_graph.astream(
                    initial_state, config=prep_config, stream_mode="custom"
                ):
                    # `chunk` is the dict our nodes wrote via get_stream_writer.
                    event = chunk.get("event")
                    if event in ("node_started", "node_done", "node_skipped"):
                        yield sse_event(event, {k: v for k, v in chunk.items() if k != "event"})
                    elif event == "error":
                        yield sse_event("error", {k: v for k, v in chunk.items() if k != "event"})
                        return
                yield sse_event("done", {"job_id": str(body.job_id), "ready": True})
        except (NoDocumentsError, NoSearchHits, NoUsablePages, CompanyNameMissing) as e:
            yield sse_event("error", {"code": type(e).__name__, "detail": str(e)})
        finally:
            # v4 SDK buffers spans; without an explicit flush, prep traces only
            # appear when a later request (e.g. next_question) nudges the queue.
            await flush_langfuse()

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=SSE_HEADERS)


# --- Phase 8/9 routes, rewritten to drive interview_graph -----------


def _thread_config(session_id: uuid.UUID, turn_index: int) -> dict[str, Any]:
    """One graph thread per (session, turn_index).

    Each turn is its own pipeline (question_generator → interrupt →
    evaluator → END). Per-turn thread_ids let a session walk forward
    cleanly without colliding with prior turn checkpoints.
    """
    return {"configurable": {"thread_id": f"{session_id}:turn_{turn_index}"}}


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
    interview_graph = request.app.state.interview_graph
    config = _with_callbacks(_thread_config(session_id, turn_index))
    initial_state: dict[str, Any] = {
        "user_id": str(user.id),
        "session_id": str(session_id),
        "round_type": row.round_type,
        "n_questions": row.n_questions,
        "turn_index": turn_index,
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
