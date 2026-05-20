import asyncio
import hashlib
import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from interview_coach.api.auth.deps import get_current_user
from interview_coach.api.documents.schemas import (
    DocumentKind,
    DocumentListItem,
    DocumentOut,
    EmbeddingStatus,
    RemapConfirmRequest,
)
from interview_coach.api.errors import blocking_sessions_http_exception
from interview_coach.db import repos
from interview_coach.db.models import User
from interview_coach.db.session import get_db
from interview_coach.ingestion import extract_text
from interview_coach.ingestion.errors import ExtractionFailed, UnsupportedFormat
from interview_coach.rag.concurrency import ingest_sema

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])


async def _embed_in_background(document_id: uuid.UUID) -> None:
    """Best-effort: log and swallow any embedding failure so a flaky
    embedder doesn't break document upload.

    Acquires the shared ``ingest_sema`` so embed and profile-build don't
    parallelise inside the api container (see Phase 19 plan).
    """
    async with ingest_sema:
        try:
            from interview_coach.rag.ingest import embed_and_store_document

            await embed_and_store_document(document_id)
        except Exception:  # noqa: BLE001
            logger.exception("background embedding failed for document %s", document_id)


# Doc is "pending" until this many seconds after creation, then flips to
# "failed" if no chunks landed. Generous to absorb chunker latency on big docs.
EMBED_PENDING_GRACE_S = 60


async def _embedding_status_for(doc: Any, session: AsyncSession) -> EmbeddingStatus:
    """Derive a document's embedding_status from chunk-count + age.

    project_doc reports ``n_a`` until mapping is confirmed — its chunking is
    deferred until ``apply_mapping`` runs, so the absence of chunks then is
    expected, not a failure.
    """
    if doc.kind == "project_doc":
        n_mappings = len(await repos.list_document_mappings(session, doc.id))
        if n_mappings == 0:
            return "n_a"
    chunks = await repos.count_grounding_chunks_for_document(session, doc.id)
    if chunks > 0:
        return "ready"
    created = doc.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    age_s = (datetime.now(UTC) - created).total_seconds()
    return "pending" if age_s < EMBED_PENDING_GRACE_S else "failed"


MAX_BYTES = 10 * 1024 * 1024  # 10 MB


@router.post("", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def upload_document(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
    response: Response,
    kind: Annotated[DocumentKind, Form()],
    file: Annotated[UploadFile, File()],
) -> DocumentOut:
    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File too large (max {MAX_BYTES // (1024 * 1024)} MB)",
        )
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file")

    try:
        text = extract_text(file.filename or "", file.content_type or "", data)
    except UnsupportedFormat as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    except ExtractionFailed as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e

    # Phase 22: dedup at upload time. Re-uploading identical content
    # collapses onto the original row with HTTP 200 instead of a duplicate.
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    existing = await repos.find_document_by_content_hash(
        session, user_id=user.id, kind=kind.value, content_hash=content_hash
    )
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        out = DocumentOut.model_validate(existing)
        out.embedding_status = await _embedding_status_for(existing, session)
        return out

    # Phase 22 Replace-CV cascade: detect *before* create_document fires
    # (which deletes the old CV row in-transaction) so we can tell apart a
    # first-time CV upload from a replacement. CV-with-same-bytes is already
    # short-circuited above by the dedup path.
    is_cv_replace = False
    if kind == DocumentKind.cv:
        prior_cvs = [
            d for d in await repos.list_documents_for_user(session, user.id) if d.kind == "cv"
        ]
        is_cv_replace = len(prior_cvs) > 0

    doc = await repos.create_document(
        session,
        user_id=user.id,
        kind=kind.value,
        filename=file.filename or "uploaded",
        content_type=file.content_type or "application/octet-stream",
        byte_size=len(data),
        raw_text=text,
        content_hash=content_hash,
    )
    if kind == DocumentKind.cv:
        if is_cv_replace:
            # The new CV invalidates everything CV-derived: the profile
            # (recomputed by prep_graph on next /prepare) and every
            # project_doc mapping (which was made against the *old*
            # profile's experiences/projects/skills). Wipe both — the
            # wizard's work-driven auto-prep will rebuild the profile and
            # walk the user through remapping each project_doc.
            #
            # JD parsed_json and company snapshots are intentionally
            # untouched — they're job-derived, not CV-derived.
            await repos.delete_profile(session, user.id)
            await repos.reset_project_doc_mappings_for_user(session, user.id)
        # CV: kick off RAG embedding only. Profile-building is owned solely
        # by prep_graph (Phase 21.1) — see note in the route docstring.
        asyncio.create_task(_embed_in_background(doc.id))
    # project_doc: chunking deferred until the prep-graph doc_mapping node
    # applies the mapping (so chunks carry the final user-edited title).
    out = DocumentOut.model_validate(doc)
    out.embedding_status = await _embedding_status_for(doc, session)
    return out


@router.get("", response_model=list[DocumentListItem])
async def list_documents(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[DocumentListItem]:
    docs = await repos.list_documents_for_user(session, user.id)
    items: list[DocumentListItem] = []
    for d in docs:
        items.append(
            DocumentListItem(
                id=d.id,
                user_id=d.user_id,
                kind=DocumentKind(d.kind),
                filename=d.filename,
                content_type=d.content_type,
                byte_size=d.byte_size,
                created_at=d.created_at,
                char_count=len(d.raw_text),
                project_title=d.project_title,
                embedding_status=await _embedding_status_for(d, session),
            )
        )
    return items


@router.get("/{document_id}", response_model=DocumentOut)
async def get_document(
    document_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> DocumentOut:
    doc = await repos.get_document(session, document_id, user.id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    out = DocumentOut.model_validate(doc)
    out.embedding_status = await _embedding_status_for(doc, session)
    return out


@router.post("/{document_id}/embed", status_code=status.HTTP_202_ACCEPTED)
async def retry_embed_document(
    document_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """Phase 22: re-schedule embedding for a document whose first attempt
    failed. Refuses for an unmapped ``project_doc`` because that kind's
    chunking is *deferred until mapping confirm* — pressing retry-embed
    there would skip the mapping step and produce chunks against a
    placeholder title. The right affordance for that state is Remap.
    """
    doc = await repos.get_document(session, document_id, user.id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    if doc.kind == "project_doc":
        mappings = await repos.list_document_mappings(session, doc.id)
        if not mappings:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "project_doc has no mapping; use remap instead",
            )
    asyncio.create_task(_embed_in_background(doc.id))
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post("/{document_id}/remap")
async def start_remap(
    document_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Phase 22: kick off an out-of-graph remap for a single ``project_doc``.

    Runs ``run_intake`` against the doc to produce a fresh suggestion.
    Returns the same payload shape the prep-graph node emits via SSE,
    so the FE ``MappingModal`` consumes one schema.

    Phase 25 (B12): the prior revert-up-front behavior silently dropped
    grounding chunks if the user opened remap and then closed the
    modal. Revert now happens inside ``confirm_remap`` on the apply
    branch only — a skip leaves the existing mapping + chunks intact.
    """
    from interview_coach.agents.nodes.doc_intake import (
        DocIntakeError,
        ProfileMissing,
        build_mapping_suggestion_payload,
        run_intake,
    )

    doc = await repos.get_document(session, document_id, user.id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    if doc.kind != "project_doc":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "remap only supports project_doc")

    try:
        intake = await run_intake(doc.id, user.id)
    except ProfileMissing as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    except DocIntakeError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e

    profile_row = await repos.get_profile(session, user.id)
    profile_json = profile_row.profile_json if profile_row is not None else None
    return build_mapping_suggestion_payload(
        document_id=doc.id,
        intake=intake,
        doc_raw_text=doc.raw_text,
        profile_json=profile_json,
        remaining=1,
    )


@router.post("/{document_id}/remap/confirm", response_model=DocumentOut)
async def confirm_remap(
    document_id: uuid.UUID,
    body: RemapConfirmRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> DocumentOut:
    """Phase 22: finish an out-of-graph remap.

    On ``apply``: reverts the prior mapping (Phase 25 B12 — moved from
    ``start_remap`` so we don't drop grounding chunks on a skip), then
    calls ``apply_mapping`` with the user's choices and returns the
    updated doc. On ``skip``: no DB writes; the existing mapping and
    grounding chunks stay intact. Both paths return the current
    ``DocumentOut`` so the FE can refresh its row.
    """
    from interview_coach.agents.nodes.doc_intake import (
        ProfileMissing,
        apply_mapping,
        revert_mapping,
    )

    doc = await repos.get_document(session, document_id, user.id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    if doc.kind != "project_doc":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "remap only supports project_doc")

    if body.action == "apply":
        assert body.title is not None and body.extracted is not None  # validator
        try:
            await revert_mapping(document_id=doc.id, user_id=user.id)
        except Exception:  # noqa: BLE001
            # Revert is best-effort — a failure here is logged but
            # doesn't block the apply. ``apply_mapping`` will either
            # overlay cleanly or surface its own validation error.
            logger.exception("revert_mapping failed during remap apply for doc=%s", doc.id)
        try:
            await apply_mapping(
                document_id=doc.id,
                user_id=user.id,
                rows=[r.model_dump() for r in body.rows],
                extracted=body.extracted,
                project_title=body.title,
            )
        except ProfileMissing as e:
            raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
        # apply_mapping persisted a new project_title; reload so the
        # response reflects it.
        await session.refresh(doc)

    out = DocumentOut.model_validate(doc)
    out.embedding_status = await _embedding_status_for(doc, session)
    return out


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """Delete the document. Reverts profile enrichments first (best effort)
    so the highlights this doc enriched go back to their bare CV state.

    Refuses with 409 ``cv_in_use`` if the user has active sessions — replacing
    the CV mid-session would orphan the session's profile context. The 409
    body carries ``blocking_session_ids`` so the FE can surface per-session
    Abandon buttons (Phase 22).
    """
    doc = await repos.get_document(session, document_id, user.id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")

    if doc.kind == "cv":
        blocking = await repos.list_active_session_ids_for_user(session, user.id)
        if blocking:
            raise blocking_sessions_http_exception(code="cv_in_use", blocking_session_ids=blocking)
        # Drop the profile that this CV grounded. Without this, profile_ready
        # stays true after CV deletion and the wizard would skip the CV step.
        await repos.delete_profile(session, user.id)

    if doc.kind == "project_doc":
        try:
            from interview_coach.agents.nodes.doc_intake import revert_mapping

            await revert_mapping(document_id=document_id, user_id=user.id)
        except Exception:  # noqa: BLE001
            logger.exception("revert_mapping failed for doc=%s", document_id)

    deleted = await repos.delete_document(session, document_id, user.id)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
