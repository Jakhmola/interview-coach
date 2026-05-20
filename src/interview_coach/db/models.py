import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    parsed_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=True
    )
    project_title: Mapped[str | None] = mapped_column(String(160), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Phase 25 (B11): set every time embedding is scheduled for this doc
    # (initial upload, apply_mapping, retry-embed). The status helper
    # treats "recently attempted" as ``pending``, so a retry after the
    # 60s grace window surfaces as a fresh attempt instead of staying
    # stuck on ``failed`` until chunks land.
    last_embed_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint("kind in ('cv', 'project_doc')", name="ck_documents_kind"),
        Index(
            "uq_documents_user_cv",
            "user_id",
            unique=True,
            postgresql_where=text("kind = 'cv'"),
            sqlite_where=text("kind = 'cv'"),
        ),
        Index(
            "uq_documents_user_kind_content_hash",
            "user_id",
            "kind",
            "content_hash",
            unique=True,
            postgresql_where=text("content_hash IS NOT NULL"),
            sqlite_where=text("content_hash IS NOT NULL"),
        ),
    )


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    parsed_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=True
    )
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint("source in ('pasted', 'url')", name="ck_jobs_source"),
        Index(
            "uq_jobs_user_content_hash",
            "user_id",
            "content_hash",
            unique=True,
            postgresql_where=text("content_hash IS NOT NULL"),
            sqlite_where=text("content_hash IS NOT NULL"),
        ),
    )


class ProfileRow(Base):
    """Persisted candidate profile built by the ProfileBuilder agent.

    Named `ProfileRow` (not `Profile`) because `Profile` is the Pydantic
    schema in `agents/schemas.py` — keeping them distinct avoids accidental
    cross-imports.
    """

    __tablename__ = "profiles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    profile_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    source_doc_ids: Mapped[list[Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SessionRow(Base):
    """Interview session: user × job × round_type, holds N turns."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    round_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    n_questions: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "round_type in ('resume_walkthrough','behavioral_star')",
            name="ck_sessions_round_type",
        ),
        CheckConstraint(
            "status in ('active','complete','abandoned')",
            name="ck_sessions_status",
        ),
    )


class TurnRow(Base):
    """One Q&A round inside a session.

    Phase 8 fills `question`, `anchors_json`, `metadata_json`.
    Phase 9 fills `answer`, `score`, `feedback`, `model_answer`.
    """

    __tablename__ = "turns"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    anchors_json: Mapped[list[Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index(
            "uq_turns_session_turn",
            "session_id",
            "turn_index",
            unique=True,
        ),
    )


class GroundingChunk(Base):
    """A single chunk of a user document with its Jina v3 embedding.

    pgvector-only at runtime; on SQLite (tests) the `embedding` column
    falls back to JSON so `Base.metadata.create_all` works. Vector
    similarity search is exercised only against Postgres.
    """

    __tablename__ = "grounding_chunks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    source_doc_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    n_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(
        Vector(1024).with_variant(JSON(), "sqlite"), nullable=False
    )
    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    # Phase 24: `text_tsv tsvector` is added by Alembic migration 0011 but
    # intentionally NOT declared on the ORM class. It is a Postgres-only
    # generated column (`GENERATED ALWAYS AS to_tsvector('english', text)
    # STORED`); SQLite has no equivalent, and SQLAlchemy can't represent a
    # generated column whose expression is dialect-specific via
    # `with_variant`. The BM25 branch in `rag/hybrid.py` references the
    # column via raw SQL, which is the only place it's used.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "source_doc_kind in ('cv','project_doc')",
            name="ck_grounding_chunks_source_doc_kind",
        ),
        Index("ix_grounding_chunks_user_kind", "user_id", "source_doc_kind"),
        Index("ix_grounding_chunks_document_id", "document_id"),
        Index(
            "uq_grounding_chunks_document_chunk",
            "document_id",
            "chunk_index",
            unique=True,
        ),
    )


class DocumentMapping(Base):
    """One row per (document, target) — how a project_doc is wired to the profile.

    A single doc can produce multiple rows (multi-select in the HITL modal:
    e.g. enriches highlight A at company X AND highlight B at company Y).
    `extracted_json` captures exactly what *this* doc contributed (tech, urls,
    description) so doc deletion can subtract precisely.
    """

    __tablename__ = "document_mappings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mapping_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    experience_idx: Mapped[int | None] = mapped_column(Integer, nullable=True)
    highlight_idx: Mapped[int | None] = mapped_column(Integer, nullable=True)
    project_idx: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extracted_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "mapping_kind in ('highlight','experience','project')",
            name="ck_document_mappings_kind",
        ),
    )


class LLMCall(Base):
    """One row per LLM call. Written by `llm.telemetry.record_call`.

    `node_name` is best-effort (NULL when no context was set). `prompt_tokens`
    and `completion_tokens` are NULL only when the provider didn't emit a
    usage block at all — llama.cpp and OpenAI both do when
    `stream_options.include_usage=true` (set via `stream_usage=True` in the
    `ChatOpenAI` factory).
    """

    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    node_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_class: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_llm_calls_ts", "ts"),
        Index("ix_llm_calls_node", "node_name", "ts"),
    )


class CompanySnapshotRow(Base):
    """LLM-compressed company research, scoped to a single job.

    `Profile` and `JobAnalysis` have Pydantic counterparts in `agents/schemas.py`;
    `CompanySnapshot` is the Pydantic model and `CompanySnapshotRow` is the table.
    """

    __tablename__ = "company_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    company_name: Mapped[str] = mapped_column(String(256), nullable=False)
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    source_urls: Mapped[list[Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
