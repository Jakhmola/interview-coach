"""phase 24: hybrid retrieval — tsvector column + GIN index on grounding_chunks

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-20

Adds a generated `text_tsv` column on `grounding_chunks` and a GIN index
so BM25 (Postgres FTS) can run alongside the existing pgvector cosine
search. Reciprocal Rank Fusion happens in Python (`rag/hybrid.py`); this
migration is pure schema.

`GENERATED ALWAYS AS … STORED` means Postgres populates the tsvector
for every existing row at column-add time — no manual backfill loop.
The trade-off is that the ALTER rewrites the table (Postgres ≤ 16 does
not skip rewrites for generated columns the way it does for constant
defaults). Single-user dev DB → negligible; flag this if anyone runs
the migration against a large dataset later.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE grounding_chunks "
        "ADD COLUMN text_tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('english', text)) STORED;"
    )
    op.execute(
        "CREATE INDEX ix_grounding_chunks_text_tsv ON grounding_chunks USING GIN (text_tsv);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_grounding_chunks_text_tsv;")
    op.execute("ALTER TABLE grounding_chunks DROP COLUMN IF EXISTS text_tsv;")
