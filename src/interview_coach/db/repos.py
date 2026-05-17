import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from interview_coach.db.models import (
    CompanySnapshotRow,
    Document,
    DocumentMapping,
    GroundingChunk,
    Job,
    ProfileRow,
    SessionRow,
    TurnRow,
    User,
)

# --- users ---


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == email.lower()))
    return result.scalar_one_or_none()


async def create_user(session: AsyncSession, email: str, hashed_password: str) -> User:
    user = User(email=email.lower(), hashed_password=hashed_password)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


# --- documents ---


async def list_documents_for_user(session: AsyncSession, user_id: uuid.UUID) -> Sequence[Document]:
    result = await session.execute(
        select(Document).where(Document.user_id == user_id).order_by(Document.created_at.desc())
    )
    return result.scalars().all()


async def get_document(
    session: AsyncSession, document_id: uuid.UUID, user_id: uuid.UUID
) -> Document | None:
    result = await session.execute(
        select(Document).where(Document.id == document_id, Document.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def delete_document(
    session: AsyncSession, document_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    """Returns True if a row was deleted."""
    result = await session.execute(
        delete(Document).where(Document.id == document_id, Document.user_id == user_id)
    )
    await session.commit()
    return (result.rowcount or 0) > 0


async def update_document_title(
    session: AsyncSession,
    document_id: uuid.UUID,
    user_id: uuid.UUID,
    project_title: str,
) -> bool:
    """Set documents.project_title for a project_doc. Returns True on hit."""
    doc = await get_document(session, document_id, user_id)
    if doc is None:
        return False
    doc.project_title = project_title
    await session.commit()
    return True


async def create_document(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    kind: str,
    filename: str,
    content_type: str,
    byte_size: int,
    raw_text: str,
) -> Document:
    """Insert a new document. For kind='cv', any existing CV for this user is
    deleted first in the same transaction (replace semantics)."""
    if kind == "cv":
        await session.execute(
            delete(Document).where(Document.user_id == user_id, Document.kind == "cv")
        )

    doc = Document(
        user_id=user_id,
        kind=kind,
        filename=filename,
        content_type=content_type,
        byte_size=byte_size,
        raw_text=raw_text,
    )
    session.add(doc)
    await session.commit()
    await session.refresh(doc)
    return doc


# --- document mappings ---


async def list_document_mappings(
    session: AsyncSession, document_id: uuid.UUID
) -> Sequence[DocumentMapping]:
    result = await session.execute(
        select(DocumentMapping)
        .where(DocumentMapping.document_id == document_id)
        .order_by(DocumentMapping.created_at.asc())
    )
    return result.scalars().all()


async def delete_document_mappings_for_document(
    session: AsyncSession, document_id: uuid.UUID
) -> int:
    result = await session.execute(
        delete(DocumentMapping).where(DocumentMapping.document_id == document_id)
    )
    await session.commit()
    return result.rowcount or 0


async def replace_document_mappings(
    session: AsyncSession,
    *,
    document_id: uuid.UUID,
    user_id: uuid.UUID,
    rows: list[dict[str, Any]],
) -> int:
    """Delete prior mappings for `document_id`, then insert new rows.

    Each row dict: ``mapping_kind``, ``experience_idx?``, ``highlight_idx?``,
    ``project_idx?``, ``extracted_json?``.
    """
    await session.execute(delete(DocumentMapping).where(DocumentMapping.document_id == document_id))
    new_rows = [
        DocumentMapping(
            document_id=document_id,
            user_id=user_id,
            mapping_kind=r["mapping_kind"],
            experience_idx=r.get("experience_idx"),
            highlight_idx=r.get("highlight_idx"),
            project_idx=r.get("project_idx"),
            extracted_json=r.get("extracted_json"),
        )
        for r in rows
    ]
    session.add_all(new_rows)
    await session.commit()
    return len(new_rows)


# --- jobs ---


async def list_jobs_for_user(session: AsyncSession, user_id: uuid.UUID) -> Sequence[Job]:
    result = await session.execute(
        select(Job).where(Job.user_id == user_id).order_by(Job.created_at.desc())
    )
    return result.scalars().all()


async def get_job(session: AsyncSession, job_id: uuid.UUID, user_id: uuid.UUID) -> Job | None:
    result = await session.execute(select(Job).where(Job.id == job_id, Job.user_id == user_id))
    return result.scalar_one_or_none()


async def delete_job(session: AsyncSession, job_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    result = await session.execute(delete(Job).where(Job.id == job_id, Job.user_id == user_id))
    await session.commit()
    return (result.rowcount or 0) > 0


async def create_job(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    source: str,
    raw_text: str,
    source_url: str | None = None,
) -> Job:
    job = Job(user_id=user_id, source=source, source_url=source_url, raw_text=raw_text)
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


async def update_job_parsed_json(
    session: AsyncSession,
    job_id: uuid.UUID,
    user_id: uuid.UUID,
    parsed: dict[str, Any],
) -> bool:
    """Set jobs.parsed_json (Phase 6 JobAnalyzer output). Returns True on hit."""
    job = await get_job(session, job_id, user_id)
    if job is None:
        return False
    job.parsed_json = parsed
    await session.commit()
    return True


# --- profiles ---


async def get_profile(session: AsyncSession, user_id: uuid.UUID) -> ProfileRow | None:
    result = await session.execute(select(ProfileRow).where(ProfileRow.user_id == user_id))
    return result.scalar_one_or_none()


async def upsert_profile(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    profile_json: dict[str, Any],
    source_doc_ids: list[str],
    model_name: str,
) -> ProfileRow:
    """One profile per user; rebuild replaces. updated_at auto-bumps via onupdate."""
    existing = await get_profile(session, user_id)
    if existing is None:
        row = ProfileRow(
            user_id=user_id,
            profile_json=profile_json,
            source_doc_ids=source_doc_ids,
            model_name=model_name,
        )
        session.add(row)
    else:
        existing.profile_json = profile_json
        existing.source_doc_ids = source_doc_ids
        existing.model_name = model_name
        row = existing
    await session.commit()
    await session.refresh(row)
    return row


# --- sessions & turns ---


async def create_session(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    job_id: uuid.UUID,
    round_type: str,
    n_questions: int,
) -> SessionRow:
    row = SessionRow(
        user_id=user_id,
        job_id=job_id,
        round_type=round_type,
        n_questions=n_questions,
        status="active",
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def get_session(
    session: AsyncSession, session_id: uuid.UUID, user_id: uuid.UUID
) -> SessionRow | None:
    result = await session.execute(
        select(SessionRow).where(SessionRow.id == session_id, SessionRow.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def list_sessions_for_user(session: AsyncSession, user_id: uuid.UUID) -> Sequence[SessionRow]:
    result = await session.execute(
        select(SessionRow)
        .where(SessionRow.user_id == user_id)
        .order_by(SessionRow.created_at.desc())
    )
    return result.scalars().all()


async def update_session_status(
    session: AsyncSession, session_id: uuid.UUID, user_id: uuid.UUID, status: str
) -> bool:
    row = await get_session(session, session_id, user_id)
    if row is None:
        return False
    row.status = status
    await session.commit()
    return True


async def list_turns_for_session(session: AsyncSession, session_id: uuid.UUID) -> Sequence[TurnRow]:
    result = await session.execute(
        select(TurnRow).where(TurnRow.session_id == session_id).order_by(TurnRow.turn_index.asc())
    )
    return result.scalars().all()


async def list_prior_focus_keys_for_user_job(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    job_id: uuid.UUID,
    round_type: str,
    limit: int = 50,
) -> list[str]:
    """Return focus_keys from this user's prior turns for this job+round, newest first.

    Joins turns → sessions to scope by (user_id, job_id, round_type). Drops rows
    whose metadata_json is NULL or missing a `focus_key`. The picker uses this
    to compute inverse-frequency weights across sessions, so a single user
    grinding the same JD over and over rotates through their experiences and
    competencies rather than re-drilling the most prominent one each time.
    """
    result = await session.execute(
        select(TurnRow.metadata_json)
        .join(SessionRow, TurnRow.session_id == SessionRow.id)
        .where(
            SessionRow.user_id == user_id,
            SessionRow.job_id == job_id,
            SessionRow.round_type == round_type,
            TurnRow.metadata_json.is_not(None),
        )
        .order_by(TurnRow.created_at.desc())
        .limit(limit)
    )
    keys: list[str] = []
    for (metadata,) in result.all():
        if isinstance(metadata, dict):
            key = metadata.get("focus_key")
            if isinstance(key, str) and key:
                keys.append(key)
    return keys


def count_focus_keys(focus_keys: list[str]) -> dict[str, int]:
    """Pure helper: turn an ordered list of focus_keys into a {key: count} map."""
    counts: dict[str, int] = {}
    for key in focus_keys:
        counts[key] = counts.get(key, 0) + 1
    return counts


async def latest_turn(session: AsyncSession, session_id: uuid.UUID) -> TurnRow | None:
    result = await session.execute(
        select(TurnRow)
        .where(TurnRow.session_id == session_id)
        .order_by(TurnRow.turn_index.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_turn(session: AsyncSession, turn_id: uuid.UUID) -> TurnRow | None:
    result = await session.execute(select(TurnRow).where(TurnRow.id == turn_id))
    return result.scalar_one_or_none()


async def update_turn_answer(session: AsyncSession, turn_id: uuid.UUID, answer: str) -> bool:
    row = await get_turn(session, turn_id)
    if row is None:
        return False
    row.answer = answer
    await session.commit()
    return True


async def update_turn_evaluation(
    session: AsyncSession,
    turn_id: uuid.UUID,
    *,
    score: int,
    feedback: str,
    model_answer: str,
) -> bool:
    row = await get_turn(session, turn_id)
    if row is None:
        return False
    row.score = score
    row.feedback = feedback
    row.model_answer = model_answer
    await session.commit()
    return True


async def update_turn_evaluation_partial(
    session: AsyncSession,
    turn_id: uuid.UUID,
    *,
    score: int,
    feedback: str,
) -> bool:
    """Persist score + feedback only — used when the model-answer call fails."""
    row = await get_turn(session, turn_id)
    if row is None:
        return False
    row.score = score
    row.feedback = feedback
    await session.commit()
    return True


async def create_turn(
    session: AsyncSession,
    *,
    session_id: uuid.UUID,
    turn_index: int,
    question: str,
    anchors: list[str],
    metadata: dict[str, Any] | None = None,
) -> TurnRow:
    row = TurnRow(
        session_id=session_id,
        turn_index=turn_index,
        question=question,
        anchors_json=anchors,
        metadata_json=metadata,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


# --- company snapshots ---


async def get_company_snapshot_by_job(
    session: AsyncSession, job_id: uuid.UUID
) -> CompanySnapshotRow | None:
    result = await session.execute(
        select(CompanySnapshotRow).where(CompanySnapshotRow.job_id == job_id)
    )
    return result.scalar_one_or_none()


# --- grounding chunks ---


async def delete_grounding_chunks_for_document(
    session: AsyncSession, document_id: uuid.UUID
) -> int:
    result = await session.execute(
        delete(GroundingChunk).where(GroundingChunk.document_id == document_id)
    )
    await session.commit()
    return result.rowcount or 0


async def count_grounding_chunks_for_document(session: AsyncSession, document_id: uuid.UUID) -> int:
    """Return the chunk count for a document. Used to derive embedding_status."""
    result = await session.execute(
        select(func.count(GroundingChunk.id)).where(GroundingChunk.document_id == document_id)
    )
    return int(result.scalar_one() or 0)


async def count_active_sessions_for_job(session: AsyncSession, job_id: uuid.UUID) -> int:
    """Active sessions referencing this job — gates DELETE /jobs/{id}."""
    result = await session.execute(
        select(func.count(SessionRow.id)).where(
            SessionRow.job_id == job_id, SessionRow.status == "active"
        )
    )
    return int(result.scalar_one() or 0)


async def count_active_sessions_for_user(session: AsyncSession, user_id: uuid.UUID) -> int:
    """Active sessions owned by this user — gates DELETE /documents/{cv_id}."""
    result = await session.execute(
        select(func.count(SessionRow.id)).where(
            SessionRow.user_id == user_id, SessionRow.status == "active"
        )
    )
    return int(result.scalar_one() or 0)


async def insert_grounding_chunks(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    document_id: uuid.UUID,
    source_doc_kind: str,
    chunks: list[dict[str, Any]],
    model_name: str,
) -> int:
    """Bulk-insert chunks for a document. Each chunk dict needs:
    `chunk_index`, `text`, `n_tokens`, `embedding` (list[float]).
    Returns count inserted.
    """
    rows = [
        GroundingChunk(
            user_id=user_id,
            document_id=document_id,
            source_doc_kind=source_doc_kind,
            chunk_index=c["chunk_index"],
            text=c["text"],
            n_tokens=c["n_tokens"],
            embedding=c["embedding"],
            model_name=model_name,
        )
        for c in chunks
    ]
    session.add_all(rows)
    await session.commit()
    return len(rows)


async def upsert_company_snapshot(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    company_name: str,
    snapshot_json: dict[str, Any],
    source_urls: list[str],
    model_name: str,
) -> CompanySnapshotRow:
    """One snapshot per job; refresh replaces."""
    existing = await get_company_snapshot_by_job(session, job_id)
    if existing is None:
        row = CompanySnapshotRow(
            job_id=job_id,
            company_name=company_name,
            snapshot_json=snapshot_json,
            source_urls=source_urls,
            model_name=model_name,
        )
        session.add(row)
    else:
        existing.company_name = company_name
        existing.snapshot_json = snapshot_json
        existing.source_urls = source_urls
        existing.model_name = model_name
        row = existing
    await session.commit()
    await session.refresh(row)
    return row
