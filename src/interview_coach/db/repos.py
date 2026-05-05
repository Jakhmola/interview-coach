import uuid
from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from interview_coach.db.models import Document, Job, User

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
