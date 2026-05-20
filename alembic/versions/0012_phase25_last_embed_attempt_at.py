"""phase 25: last_embed_attempt_at on documents

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-21

Adds a nullable timestamptz tracking the most recent time embedding
was scheduled for a document. Used by the embedding_status derivation
in ``api/documents/routes.py`` so a retry-embed shortly after the 60s
grace window still surfaces as ``pending`` to the UI instead of
staying stuck on ``failed`` until chunks land.

Nullable, no backfill — ``NULL`` is treated as "never attempted",
which the existing age-based fallback handles correctly.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("last_embed_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("documents", "last_embed_attempt_at")
