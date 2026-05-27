import uuid
from collections.abc import Sequence
from dataclasses import dataclass
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


async def list_unmapped_project_docs_for_user(
    session: AsyncSession, user_id: uuid.UUID
) -> Sequence[Document]:
    """Phase 21.1: project_docs for a user that have no document_mappings rows.

    Used by ``node_doc_mapping`` in prep_graph to pick the next project_doc
    to surface to the user for HITL confirmation. Returned oldest-first so
    multi-doc setups feel sequential ("doc 1 of 3, doc 2 of 3, …").
    """
    mapped_doc_ids = select(DocumentMapping.document_id).distinct().scalar_subquery()
    result = await session.execute(
        select(Document)
        .where(
            Document.user_id == user_id,
            Document.kind == "project_doc",
            Document.id.notin_(mapped_doc_ids),
        )
        .order_by(Document.created_at.asc())
    )
    return result.scalars().all()


async def list_document_mappings_for_user(
    session: AsyncSession, user_id: uuid.UUID
) -> Sequence[DocumentMapping]:
    """All ``document_mappings`` rows for a user, oldest-first.

    Phase 21.1: ``build_profile`` consumes these after re-extracting from
    the CV so the freshly-built profile inherits prior project_doc
    enrichments. Without this, a CV re-extract would silently wipe every
    mapping the user had previously confirmed.
    """
    result = await session.execute(
        select(DocumentMapping)
        .where(DocumentMapping.user_id == user_id)
        .order_by(DocumentMapping.created_at.asc())
    )
    return result.scalars().all()


async def list_document_mapping_doc_ids_for_user(
    session: AsyncSession, user_id: uuid.UUID
) -> list[uuid.UUID]:
    """Phase 25 (B2): distinct ``document_id`` over a user's mappings.

    Used by ``node_profile_builder`` to compute its cache key against
    only the documents that actually contribute to the profile (the CV
    plus every project_doc whose mapping has been confirmed). Comparing
    the full ``documents`` list — as the prior implementation did —
    flips the key to a miss the moment a project_doc lands on disk,
    *before* its mapping is applied, forcing a needless profile
    rebuild on every project_doc upload.
    """
    result = await session.execute(
        select(DocumentMapping.document_id).where(DocumentMapping.user_id == user_id).distinct()
    )
    return list(result.scalars().all())


async def current_profile_doc_ids(session: AsyncSession, user_id: uuid.UUID) -> list[str]:
    """Phase 26: the canonical **Profile document set** — the single source
    of truth for the ``profile_builder`` cache key.

    CV doc ids ∪ distinct confirmed-mapping document ids, normalized to
    ``str`` and sorted. Computed at both write (``build_profile``,
    ``apply_mapping``, ``revert_mapping``) and read (``node_profile_builder``)
    so ``profiles.source_doc_ids`` is a pure function of current DB state
    rather than an incrementally-folded value that can drift.
    """
    docs = await list_documents_for_user(session, user_id)
    cv_ids = {str(d.id) for d in docs if d.kind == "cv"}
    mapped_ids = await list_document_mapping_doc_ids_for_user(session, user_id)
    return sorted(cv_ids | {str(x) for x in mapped_ids})


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
    content_hash: str | None = None,
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
        content_hash=content_hash,
    )
    session.add(doc)
    await session.commit()
    await session.refresh(doc)
    return doc


async def mark_embed_attempt(session: AsyncSession, document_id: uuid.UUID) -> None:
    """Phase 25 (B11): stamp ``documents.last_embed_attempt_at = now()``
    so the embedding_status derivation treats a recently-scheduled
    embed as ``pending`` instead of inheriting the prior ``failed``
    status until chunks land. Called at the *start* of every embed
    attempt — upload, apply_mapping, retry-embed."""
    from datetime import UTC, datetime

    await session.execute(
        Document.__table__.update()
        .where(Document.id == document_id)
        .values(last_embed_attempt_at=datetime.now(UTC))
    )
    await session.commit()


async def find_document_by_content_hash(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    kind: str,
    content_hash: str,
) -> Document | None:
    """Phase 22: dedup helper for ``POST /documents``.

    Returns an existing doc when ``(user_id, kind, content_hash)`` already
    matches a row, so re-uploading identical content collapses onto the
    original instead of inserting a duplicate.
    """
    result = await session.execute(
        select(Document).where(
            Document.user_id == user_id,
            Document.kind == kind,
            Document.content_hash == content_hash,
        )
    )
    return result.scalar_one_or_none()


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


async def reset_project_doc_mappings_for_user(session: AsyncSession, user_id: uuid.UUID) -> int:
    """Phase 22: Replace-CV cascade. Drop every ``document_mappings`` row for
    the user and null the ``project_title`` on each ``project_doc`` so the
    next mapping pass starts fresh against the rebuilt profile. Returns the
    number of mapping rows removed. Idempotent. Project_doc chunks are left
    in place — ``apply_mapping``'s embed step replaces them when the user
    confirms the new mapping."""
    deleted = await session.execute(
        delete(DocumentMapping).where(DocumentMapping.user_id == user_id)
    )
    await session.execute(
        Document.__table__.update()
        .where(Document.user_id == user_id, Document.kind == "project_doc")
        .values(project_title=None)
    )
    await session.commit()
    return deleted.rowcount or 0


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
    content_hash: str | None = None,
) -> Job:
    job = Job(
        user_id=user_id,
        source=source,
        source_url=source_url,
        raw_text=raw_text,
        content_hash=content_hash,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


async def find_job_by_content_hash(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    content_hash: str,
) -> Job | None:
    """Phase 22: dedup helper for ``POST /jobs``.

    Returns the existing job for ``(user_id, content_hash)`` so repeated
    pastes of the same JD collapse onto one row (and one prep checkpoint
    thread).
    """
    result = await session.execute(
        select(Job).where(Job.user_id == user_id, Job.content_hash == content_hash)
    )
    return result.scalar_one_or_none()


async def find_job_by_source_url(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    source_url: str,
) -> Job | None:
    """Phase 22: URL-path dedup. The URL is normalized by the caller
    (lower-cased, trailing-slash-stripped) before lookup."""
    result = await session.execute(
        select(Job).where(Job.user_id == user_id, Job.source_url == source_url)
    )
    return result.scalar_one_or_none()


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


async def update_job_raw_text(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    user_id: uuid.UUID,
    raw_text: str,
    content_hash: str,
    source_url: str | None = None,
) -> Job | None:
    """Phase 22: ``PATCH /jobs/{id}`` re-analyze path.

    Replaces ``raw_text``, refreshes ``content_hash``, and optionally swaps
    in a new ``source_url``. Nukes ``parsed_json`` so the next ``/prepare``
    re-runs the job analyzer instead of using the stale cache. The caller
    is responsible for clearing the company snapshot via
    ``delete_company_snapshot_for_job``.
    """
    job = await get_job(session, job_id, user_id)
    if job is None:
        return None
    job.raw_text = raw_text
    job.content_hash = content_hash
    if source_url is not None:
        job.source_url = source_url
    job.parsed_json = None
    await session.commit()
    await session.refresh(job)
    return job


async def delete_company_snapshot_for_job(session: AsyncSession, job_id: uuid.UUID) -> bool:
    """Phase 22: drop the company snapshot for a job so the next
    ``/prepare`` re-runs the company researcher. Idempotent."""
    existing = await get_company_snapshot_by_job(session, job_id)
    if existing is None:
        return False
    await session.delete(existing)
    await session.commit()
    return True


# --- profiles ---


async def get_profile(session: AsyncSession, user_id: uuid.UUID) -> ProfileRow | None:
    result = await session.execute(select(ProfileRow).where(ProfileRow.user_id == user_id))
    return result.scalar_one_or_none()


async def delete_profile(session: AsyncSession, user_id: uuid.UUID) -> bool:
    """Drop a user's profile row. Used when the CV that grounded it is deleted."""
    existing = await get_profile(session, user_id)
    if existing is None:
        return False
    await session.delete(existing)
    await session.commit()
    return True


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


async def list_active_session_ids_for_user(
    session: AsyncSession, user_id: uuid.UUID
) -> list[uuid.UUID]:
    """Phase 22: surface the offending session ids in the 409 body so the
    FE can render per-session Abandon buttons. Newest first so the most
    recent session appears at the top of the blocking card."""
    result = await session.execute(
        select(SessionRow.id)
        .where(SessionRow.user_id == user_id, SessionRow.status == "active")
        .order_by(SessionRow.created_at.desc())
    )
    return [row for (row,) in result.all()]


async def list_active_session_ids_for_job(
    session: AsyncSession, job_id: uuid.UUID
) -> list[uuid.UUID]:
    """Phase 22: same shape as the per-user variant, scoped to a job."""
    result = await session.execute(
        select(SessionRow.id)
        .where(SessionRow.job_id == job_id, SessionRow.status == "active")
        .order_by(SessionRow.created_at.desc())
    )
    return [row for (row,) in result.all()]


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


# --- prep readiness (Phase 30) -------------------------------------------


@dataclass
class PrepReadiness:
    """The "ready to practice?" rollup for one ``(user, job)``.

    One owner for the readiness rule that ``GET /sessions/prepare/status``
    used to compute inline in the HTTP handler. Carries the raw ``profile`` /
    ``job`` / ``snapshot`` rows so the route can still build its
    ``?detail=true`` payload off the same reads.
    """

    job: Job
    profile: ProfileRow | None
    snapshot: CompanySnapshotRow | None
    has_cv: bool
    profile_ready: bool
    job_analyzed: bool
    company_researched: bool
    missing: list[str]
    can_start: bool
    unmapped_project_doc_count: int


async def prep_readiness(
    session: AsyncSession, user_id: uuid.UUID, job_id: uuid.UUID
) -> PrepReadiness | None:
    """Gather everything the setup flow needs to decide whether the user can
    start an interview for ``job_id``. Returns ``None`` when the job doesn't
    exist (the route maps that to 404).

    Five sequential reads on the request-scoped session — async sessions
    aren't concurrency-safe, so no ``gather``. The win is locality (one owner
    for the rule), not parallelism. A degraded company snapshot still counts
    as ``company_researched`` (a placeholder row exists), matching the
    pre-Phase-30 behavior.
    """
    job = await get_job(session, job_id, user_id)
    if job is None:
        return None

    docs = await list_documents_for_user(session, user_id)
    profile = await get_profile(session, user_id)
    snapshot = await get_company_snapshot_by_job(session, job_id)
    unmapped = await list_unmapped_project_docs_for_user(session, user_id)

    has_cv = any(d.kind == "cv" for d in docs)
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

    return PrepReadiness(
        job=job,
        profile=profile,
        snapshot=snapshot,
        has_cv=has_cv,
        profile_ready=profile_ready,
        job_analyzed=job_analyzed,
        company_researched=company_researched,
        missing=missing,
        can_start=not missing,
        unmapped_project_doc_count=len(unmapped),
    )


# --- Phase 22: account reset ---------------------------------------------


async def list_all_session_ids_for_user(
    session: AsyncSession, user_id: uuid.UUID
) -> list[uuid.UUID]:
    """Every session id the user owns, regardless of status. Used by the
    account-reset path to enumerate checkpoint threads to clean up — both
    in-flight and historical, since the saver doesn't distinguish."""
    result = await session.execute(select(SessionRow.id).where(SessionRow.user_id == user_id))
    return [row for (row,) in result.all()]


async def list_job_ids_for_user(session: AsyncSession, user_id: uuid.UUID) -> list[uuid.UUID]:
    """Every job id the user owns. Used to enumerate ``prep:{user}:{job}``
    checkpoint threads for cleanup before the DB cascade removes the rows."""
    result = await session.execute(select(Job.id).where(Job.user_id == user_id))
    return [row for (row,) in result.all()]


async def reset_user_data(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Phase 22 — option (b) account reset: wipe everything the user owns,
    keep the ``users`` row + auth so the user stays logged in with an empty
    account. Cascades on ``users.id`` handle grounding_chunks,
    document_mappings, company_snapshots, turns, evaluations, llm_calls —
    we only need to clear the top-level owned tables.

    Caller is responsible for clearing langgraph checkpoint threads
    *before* this runs, because thread cleanup needs the session + job ids
    that are about to be deleted.
    """
    await session.execute(delete(SessionRow).where(SessionRow.user_id == user_id))
    await session.execute(delete(Document).where(Document.user_id == user_id))
    await session.execute(delete(Job).where(Job.user_id == user_id))
    await session.execute(delete(ProfileRow).where(ProfileRow.user_id == user_id))
    await session.commit()
