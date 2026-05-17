import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, Field
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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])


async def _embed_in_background(document_id: uuid.UUID) -> None:
    """Best-effort: log and swallow any embedding failure so a flaky
    embedder doesn't break document upload."""
    try:
        from interview_coach.rag.ingest import embed_and_store_document

        await embed_and_store_document(document_id)
    except Exception:  # noqa: BLE001
        logger.exception("background embedding failed for document %s", document_id)


# Single-flight guard for profile builds. Process-local — adequate for the
# single-worker compose setup; a multi-worker deploy would want a row-lock
# on profiles.user_id.
_profile_build_in_flight: set[uuid.UUID] = set()


async def _profile_build_in_background(user_id: uuid.UUID) -> None:
    """Run the standalone profile_builder for a user. Idempotent + best-effort.

    Skips re-entrant calls so a user mashing the upload button can't queue
    multiple builds. Errors are logged and swallowed — a failed build is
    surfaced to the UI as ``profile_ready=false`` via /sessions/prepare/status.
    """
    if user_id in _profile_build_in_flight:
        logger.info("profile build already in flight for user=%s; skipping", user_id)
        return
    _profile_build_in_flight.add(user_id)
    try:
        from interview_coach.agents.nodes.profile_builder import build_profile

        await build_profile(user_id)
    except Exception:  # noqa: BLE001
        logger.exception("background profile build failed for user %s", user_id)
    finally:
        _profile_build_in_flight.discard(user_id)


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


class DocIntakeSuggestionOut(BaseModel):
    mapping_kind: Literal["highlight", "experience", "project"]
    experience_idx: int | None = None
    highlight_idx: int | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""


class DocIntakeExtractedOut(BaseModel):
    tech_stack: list[str] = Field(default_factory=list)
    description: str | None = None
    urls: list[str] = Field(default_factory=list)


class ProfileHighlightOut(BaseModel):
    highlight_idx: int
    text: str


class ProfileExperienceOut(BaseModel):
    experience_idx: int
    company: str
    role: str
    highlights: list[ProfileHighlightOut] = Field(default_factory=list)


class MappingSuggestionResponse(BaseModel):
    document_id: uuid.UUID
    title: str
    preview: str
    extracted: DocIntakeExtractedOut
    suggestions: list[DocIntakeSuggestionOut] = Field(default_factory=list)
    experiences: list[ProfileExperienceOut] = Field(default_factory=list)


class MappingRowIn(BaseModel):
    mapping_kind: Literal["highlight", "experience", "project"]
    experience_idx: int | None = None
    highlight_idx: int | None = None
    project_idx: int | None = None


class ApplyMappingRequest(BaseModel):
    title: str | None = Field(
        default=None,
        description=(
            "Optional user-edited title. Falls back to whatever was stored "
            "by the prior /mapping-suggestion call."
        ),
    )
    rows: list[MappingRowIn] = Field(default_factory=list)
    extracted: DocIntakeExtractedOut = Field(default_factory=DocIntakeExtractedOut)


class ApplyMappingResponse(BaseModel):
    document_id: uuid.UUID
    title: str
    n_rows: int


PREVIEW_CHARS = 500


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
        # CV: kick off RAG embedding AND profile build in parallel so the
        # workspace wizard can advance past Stage 1 without a separate click.
        asyncio.create_task(_embed_in_background(doc.id))
        asyncio.create_task(_profile_build_in_background(user.id))
    # project_doc: chunking deferred until /mapping confirms (so chunks
    # carry the final user-edited project_title).
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


@router.get(
    "/{document_id}/mapping-suggestion",
    response_model=MappingSuggestionResponse,
)
async def get_mapping_suggestion(
    document_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
) -> MappingSuggestionResponse:
    """Run the LLM intake call and return title/extract/suggestions for the modal.

    Only valid for ``kind='project_doc'``. The call is idempotent — re-fetching
    just re-runs the LLM. The user may edit the title before posting /mapping.
    """
    from interview_coach.agents.nodes.doc_intake import (
        DocIntakeError,
        ProfileMissing,
        run_intake,
    )

    try:
        result = await run_intake(document_id, user.id)
    except ProfileMissing as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    except DocIntakeError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e

    from interview_coach.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as s:
        doc = await repos.get_document(s, document_id, user.id)
        profile_row = await repos.get_profile(s, user.id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    preview = doc.raw_text[:PREVIEW_CHARS]

    experiences_out: list[ProfileExperienceOut] = []
    if profile_row is not None:
        for i, exp in enumerate(profile_row.profile_json.get("experiences") or []):
            highlights_out: list[ProfileHighlightOut] = []
            for j, hl in enumerate(exp.get("highlights") or []):
                if isinstance(hl, dict):
                    highlights_out.append(
                        ProfileHighlightOut(highlight_idx=j, text=str(hl.get("text") or ""))
                    )
            experiences_out.append(
                ProfileExperienceOut(
                    experience_idx=i,
                    company=str(exp.get("company") or ""),
                    role=str(exp.get("role") or ""),
                    highlights=highlights_out,
                )
            )

    return MappingSuggestionResponse(
        document_id=document_id,
        title=result.title,
        preview=preview,
        extracted=DocIntakeExtractedOut(**result.extracted.model_dump()),
        suggestions=[DocIntakeSuggestionOut(**s.model_dump()) for s in result.suggestions],
        experiences=experiences_out,
    )


@router.post(
    "/{document_id}/mapping",
    response_model=ApplyMappingResponse,
)
async def post_mapping(
    document_id: uuid.UUID,
    body: ApplyMappingRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> ApplyMappingResponse:
    """Apply the user-confirmed mapping rows. Triggers chunking + embedding."""
    from interview_coach.agents.nodes.doc_intake import ProfileMissing, apply_mapping
    from interview_coach.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as s:
        doc = await repos.get_document(s, document_id, user.id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    if doc.kind != "project_doc":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "mapping endpoint is only for project_doc")

    if body.title:
        new_title = body.title.strip()[:160]
        if new_title and new_title != (doc.project_title or ""):
            async with AsyncSessionLocal() as s:
                await repos.update_document_title(s, document_id, user.id, new_title)
                doc = await repos.get_document(s, document_id, user.id)
    title = (doc.project_title if doc else None) or "Project"

    rows: list[dict[str, Any]] = [
        {
            "mapping_kind": r.mapping_kind,
            "experience_idx": r.experience_idx,
            "highlight_idx": r.highlight_idx,
            "project_idx": r.project_idx,
        }
        for r in body.rows
    ]

    try:
        n = await apply_mapping(
            document_id=document_id,
            user_id=user.id,
            rows=rows,
            extracted=body.extracted.model_dump(),
            project_title=title,
        )
    except ProfileMissing as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e

    return ApplyMappingResponse(document_id=document_id, title=title, n_rows=n)


@router.post("/{document_id}/rebuild-profile", status_code=status.HTTP_202_ACCEPTED)
async def rebuild_profile(
    document_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, str]:
    """Re-schedule the standalone profile build for this user's CV.

    Idempotent + single-flight (a concurrent rebuild is a no-op). The
    document_id must reference the user's CV; project_doc rebuilds aren't
    supported here — those go through the mapping flow.
    """
    doc = await repos.get_document(session, document_id, user.id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    if doc.kind != "cv":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "rebuild only applies to CV")

    asyncio.create_task(_profile_build_in_background(user.id))
    return {"status": "scheduled"}


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
