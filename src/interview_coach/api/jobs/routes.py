import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from interview_coach.api.auth.deps import get_current_user
from interview_coach.api.jobs.schemas import (
    JobCreateRequest,
    JobListItem,
    JobOut,
    JobSource,
)
from interview_coach.config import settings
from interview_coach.db import repos
from interview_coach.db.models import User
from interview_coach.db.session import get_db
from interview_coach.ingestion.errors import FetchFailed, KeyMissing
from interview_coach.ingestion.web import fetch_url_text

router = APIRouter(prefix="/jobs", tags=["jobs"])

MAX_TEXT_CHARS = 50_000
PREVIEW_CHARS = 200


@router.post("", response_model=JobOut, status_code=status.HTTP_201_CREATED)
async def create_job(
    body: JobCreateRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> JobOut:
    if body.text is not None:
        text = body.text.strip()
        if not text:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty text")
        if len(text) > MAX_TEXT_CHARS:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"Text too long (max {MAX_TEXT_CHARS:,} chars)",
            )
        job = await repos.create_job(
            session, user_id=user.id, source=JobSource.pasted.value, raw_text=text
        )
        return JobOut.model_validate(job)

    # URL path
    assert body.url is not None
    url = str(body.url)
    try:
        text = await fetch_url_text(url, settings.tavily_api_key)
    except KeyMissing as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e)) from e
    except FetchFailed as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e

    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS]

    job = await repos.create_job(
        session,
        user_id=user.id,
        source=JobSource.url.value,
        raw_text=text,
        source_url=url,
    )
    return JobOut.model_validate(job)


@router.get("", response_model=list[JobListItem])
async def list_jobs(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[JobListItem]:
    jobs = await repos.list_jobs_for_user(session, user.id)
    return [
        JobListItem(
            id=j.id,
            user_id=j.user_id,
            source=JobSource(j.source),
            source_url=j.source_url,
            created_at=j.created_at,
            char_count=len(j.raw_text),
            preview=j.raw_text[:PREVIEW_CHARS],
        )
        for j in jobs
    ]


@router.get("/{job_id}", response_model=JobOut)
async def get_job(
    job_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> JobOut:
    job = await repos.get_job(session, job_id, user.id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return JobOut.model_validate(job)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(
    job_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """Delete a JD. Refuses with 409 ``job_in_use`` if any active session
    references it — deleting then would orphan the session's job context."""
    # 404 before 409: don't leak existence of someone else's job.
    job = await repos.get_job(session, job_id, user.id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")

    active = await repos.count_active_sessions_for_job(session, job_id)
    if active > 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "job_in_use")

    deleted = await repos.delete_job(session, job_id, user.id)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
