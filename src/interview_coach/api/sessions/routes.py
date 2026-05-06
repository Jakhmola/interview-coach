"""Sessions + interview streaming endpoints (Phase 8)."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from interview_coach.agents.nodes.question_generator import (
    GenerationPrereqsMissing,
    stream_question,
)
from interview_coach.agents.streaming_json import StreamingJsonError
from interview_coach.api.auth.deps import get_current_user
from interview_coach.api.sessions.schemas import (
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
    ok = await repos.update_session_status(session, session_id, user.id, "abandoned")
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session_not_found")
    row = await repos.get_session(session, session_id, user.id)
    assert row is not None
    return SessionOut.model_validate(row)


@router.post("/{session_id}/next_question")
async def next_question(
    session_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
    """SSE stream of the next question's tokens."""
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

    async def event_stream() -> AsyncIterator[bytes]:
        try:
            async for kind, data in stream_question(session_id=session_id, user_id=user.id):
                if kind == "token":
                    yield sse_event("token", data)
                elif kind == "done":
                    yield sse_event("done", data)
        except GenerationPrereqsMissing as e:
            logger.warning("Generation prereqs missing for session=%s: %s", session_id, e)
            yield sse_event("error", {"code": str(e)})
        except StreamingJsonError as e:
            logger.exception("Streaming JSON failed for session=%s", session_id)
            yield sse_event("error", {"code": "streaming_json_error", "detail": str(e)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
