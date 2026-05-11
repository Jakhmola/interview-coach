"""phase 14.1: project_title on documents + document_mappings table

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("project_title", sa.String(length=160), nullable=True),
    )

    op.create_table(
        "document_mappings",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("mapping_kind", sa.String(length=16), nullable=False),
        sa.Column("experience_idx", sa.Integer(), nullable=True),
        sa.Column("highlight_idx", sa.Integer(), nullable=True),
        sa.Column("project_idx", sa.Integer(), nullable=True),
        sa.Column("extracted_json", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            ondelete="CASCADE",
            name="fk_document_mappings_document_id",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_document_mappings_user_id",
        ),
        sa.CheckConstraint(
            "mapping_kind in ('highlight','experience','project')",
            name="ck_document_mappings_kind",
        ),
    )
    op.create_index(
        "ix_document_mappings_document_id",
        "document_mappings",
        ["document_id"],
    )
    op.create_index(
        "ix_document_mappings_user_id",
        "document_mappings",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_document_mappings_user_id", table_name="document_mappings")
    op.drop_index("ix_document_mappings_document_id", table_name="document_mappings")
    op.drop_table("document_mappings")
    op.drop_column("documents", "project_title")
