import asyncio
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
)
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

    doc = await repos.create_document(
        session,
        user_id=user.id,
        kind=kind.value,
        filename=file.filename or "uploaded",
        content_type=file.content_type or "application/octet-stream",
        byte_size=len(data),
        raw_text=text,
    )
    if kind == DocumentKind.cv:
        # CV: kick off RAG embedding only. Profile-building is owned solely
        # by prep_graph (Phase 21.1) — see note in the route docstring.
        # The wizard's Stage 2 (JD save) fires /sessions/prepare which runs
        # profile_builder synchronously, so the user sees deterministic
        # progress rather than a silent background race.
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


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """Delete the document. Reverts profile enrichments first (best effort)
    so the highlights this doc enriched go back to their bare CV state.

    Refuses with 409 ``cv_in_use`` if the user has active sessions — replacing
    the CV mid-session would orphan the session's profile context.
    """
    doc = await repos.get_document(session, document_id, user.id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")

    if doc.kind == "cv":
        active = await repos.count_active_sessions_for_user(session, user.id)
        if active > 0:
            raise HTTPException(status.HTTP_409_CONFLICT, "cv_in_use")
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
