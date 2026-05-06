"""company_snapshots

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-06

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "company_snapshots",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("company_name", sa.String(length=256), nullable=False),
        sa.Column("snapshot_json", postgresql.JSONB(), nullable=False),
        sa.Column("source_urls", postgresql.JSONB(), nullable=False),
        sa.Column("model_name", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], ondelete="CASCADE", name="fk_company_snapshots_job_id"
        ),
        sa.UniqueConstraint("job_id", name="uq_company_snapshots_job_id"),
    )
    op.create_index("ix_company_snapshots_job_id", "company_snapshots", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_company_snapshots_job_id", table_name="company_snapshots")
    op.drop_table("company_snapshots")
