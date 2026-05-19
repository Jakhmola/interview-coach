import hashlib
import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from interview_coach.api.auth.deps import get_current_user
from interview_coach.api.errors import blocking_sessions_http_exception
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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])

MAX_TEXT_CHARS = 50_000
PREVIEW_CHARS = 200


def _normalize_url(raw: str) -> str:
    """Lower-case host+path and strip trailing slash so paste-vs-paste
    of the same JD URL collapses onto one row regardless of cosmetic
    casing/slash differences."""
    return raw.strip().lower().rstrip("/")


@router.post("", response_model=JobOut, status_code=status.HTTP_201_CREATED)
async def create_job(
    body: JobCreateRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
    response: Response,
) -> JobOut:
    """Create a JD. Phase 22: re-pasting the same JD text (or re-submitting
    the same URL) returns the existing row with HTTP 200 instead of
    inserting a duplicate. URL-path dedup matches normalized
    ``source_url`` first; the text-hash check covers the case where the
    URL changed but the fetched body did not."""
    if body.text is not None:
        text = body.text.strip()
        if not text:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty text")
        if len(text) > MAX_TEXT_CHARS:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"Text too long (max {MAX_TEXT_CHARS:,} chars)",
            )
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        existing = await repos.find_job_by_content_hash(
            session, user_id=user.id, content_hash=content_hash
        )
        if existing is not None:
            response.status_code = status.HTTP_200_OK
            return JobOut.model_validate(existing)

        job = await repos.create_job(
            session,
            user_id=user.id,
            source=JobSource.pasted.value,
            raw_text=text,
            content_hash=content_hash,
        )
        return JobOut.model_validate(job)

    # URL path
    assert body.url is not None
    raw_url = str(body.url)
    normalized = _normalize_url(raw_url)
    existing_by_url = await repos.find_job_by_source_url(
        session, user_id=user.id, source_url=normalized
    )
    if existing_by_url is not None:
        response.status_code = status.HTTP_200_OK
        return JobOut.model_validate(existing_by_url)

    try:
        text = await fetch_url_text(raw_url, settings.tavily_api_key)
    except KeyMissing as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e)) from e
    except FetchFailed as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e

    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS]

    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    existing_by_hash = await repos.find_job_by_content_hash(
        session, user_id=user.id, content_hash=content_hash
    )
    if existing_by_hash is not None:
        response.status_code = status.HTTP_200_OK
        return JobOut.model_validate(existing_by_hash)

    job = await repos.create_job(
        session,
        user_id=user.id,
        source=JobSource.url.value,
        raw_text=text,
        source_url=normalized,
        content_hash=content_hash,
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


@router.patch("/{job_id}", response_model=JobOut)
async def patch_job(
    job_id: uuid.UUID,
    body: JobCreateRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> JobOut:
    """Phase 22: re-analyze a JD. Replace ``raw_text`` (either from a new
    paste or by re-fetching a new URL) and clear both the parsed analysis
    and the company snapshot so the next ``/prepare`` re-runs the
    analyzer and researcher. Same body shape as ``POST /jobs`` —
    exactly one of ``text`` or ``url``.

    The active-session check from delete intentionally does *not* apply
    here: changing JD text mid-session is a self-inflicted footgun the
    user opted into, and revoking the option would force them through
    delete-and-recreate (which IS blocked by active sessions). Manage's
    UI funnels this through a typo-fix affordance, not a "throw the JD
    away" one.
    """
    job = await repos.get_job(session, job_id, user.id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")

    if body.text is not None:
        text = body.text.strip()
        if not text:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty text")
        if len(text) > MAX_TEXT_CHARS:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"Text too long (max {MAX_TEXT_CHARS:,} chars)",
            )
        new_url: str | None = None
    else:
        assert body.url is not None
        new_url = _normalize_url(str(body.url))
        try:
            text = await fetch_url_text(str(body.url), settings.tavily_api_key)
        except KeyMissing as e:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e)) from e
        except FetchFailed as e:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e
        if len(text) > MAX_TEXT_CHARS:
            text = text[:MAX_TEXT_CHARS]

    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    updated = await repos.update_job_raw_text(
        session,
        job_id=job_id,
        user_id=user.id,
        raw_text=text,
        content_hash=content_hash,
        source_url=new_url,
    )
    if updated is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    # Snapshot clear is best-effort idempotent: if there was no snapshot
    # (job was created but never prepared), nothing to drop.
    await repos.delete_company_snapshot_for_job(session, job_id)
    return JobOut.model_validate(updated)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(
    job_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """Delete a JD. Refuses with 409 ``job_in_use`` if any active session
    references it — deleting then would orphan the session's job context.

    On successful delete, also drops the prep_graph checkpoint thread
    ``prep:{user_id}:{job_id}`` so a future job with the same id (or
    just stale rows in the saver) can't replay this job's prep state.
    Saver failures are swallowed — a leaked thread is a smaller bug
    than a 500 on delete (Phase 22).
    """
    # 404 before 409: don't leak existence of someone else's job.
    job = await repos.get_job(session, job_id, user.id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")

    blocking = await repos.list_active_session_ids_for_job(session, job_id)
    if blocking:
        raise blocking_sessions_http_exception(code="job_in_use", blocking_session_ids=blocking)

    deleted = await repos.delete_job(session, job_id, user.id)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")

    # Best-effort prep-thread cleanup. The compiled prep_graph and its
    # AsyncSqliteSaver share a single connection bound to the app's
    # event loop, so we delete inline rather than spawning a task.
    checkpointer = getattr(request.app.state, "checkpointer", None)
    if checkpointer is not None:
        thread_id = f"prep:{user.id}:{job_id}"
        try:
            await checkpointer.adelete_thread(thread_id)
        except Exception:  # noqa: BLE001
            logger.exception("prep checkpoint cleanup failed for %s", thread_id)

    return Response(status_code=status.HTTP_204_NO_CONTENT)
