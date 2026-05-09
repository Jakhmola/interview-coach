"""grounding chunks (pgvector)

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-09

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    op.create_table(
        "grounding_chunks",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("source_doc_kind", sa.String(length=32), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("n_tokens", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=False),
        sa.Column("model_name", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_grounding_chunks_user_id",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            ondelete="CASCADE",
            name="fk_grounding_chunks_document_id",
        ),
        sa.CheckConstraint(
            "source_doc_kind in ('cv','project_doc')",
            name="ck_grounding_chunks_source_doc_kind",
        ),
        sa.UniqueConstraint(
            "document_id", "chunk_index", name="uq_grounding_chunks_document_chunk"
        ),
    )
    op.create_index(
        "ix_grounding_chunks_user_kind",
        "grounding_chunks",
        ["user_id", "source_doc_kind"],
    )
    op.create_index(
        "ix_grounding_chunks_document_id",
        "grounding_chunks",
        ["document_id"],
    )
    # HNSW for cosine ANN. Parameter-free defaults are fine at our scale.
    op.execute(
        "CREATE INDEX ix_grounding_chunks_embedding_hnsw "
        "ON grounding_chunks USING hnsw (embedding vector_cosine_ops);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_grounding_chunks_embedding_hnsw;")
    op.drop_index("ix_grounding_chunks_document_id", table_name="grounding_chunks")
    op.drop_index("ix_grounding_chunks_user_kind", table_name="grounding_chunks")
    op.drop_table("grounding_chunks")
