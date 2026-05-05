import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from interview_coach.api.auth.deps import get_current_user
from interview_coach.api.documents.schemas import DocumentKind, DocumentListItem, DocumentOut
from interview_coach.db import repos
from interview_coach.db.models import User
from interview_coach.db.session import get_db
from interview_coach.ingestion import extract_text
from interview_coach.ingestion.errors import ExtractionFailed, UnsupportedFormat

router = APIRouter(prefix="/documents", tags=["documents"])

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
    return DocumentOut.model_validate(doc)


@router.get("", response_model=list[DocumentListItem])
async def list_documents(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[DocumentListItem]:
    docs = await repos.list_documents_for_user(session, user.id)
    return [
        DocumentListItem(
            id=d.id,
            user_id=d.user_id,
            kind=DocumentKind(d.kind),
            filename=d.filename,
            content_type=d.content_type,
            byte_size=d.byte_size,
            created_at=d.created_at,
            char_count=len(d.raw_text),
        )
        for d in docs
    ]


@router.get("/{document_id}", response_model=DocumentOut)
async def get_document(
    document_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> DocumentOut:
    doc = await repos.get_document(session, document_id, user.id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return DocumentOut.model_validate(doc)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    deleted = await repos.delete_document(session, document_id, user.id)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
