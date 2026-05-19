"""phase 22: content_hash on documents + jobs for upload-time dedup

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-19

Adds a SHA-256 hex column on `documents` and `jobs`, backfills it from
existing `raw_text`, and creates partial unique indexes so future
re-uploads of identical content collapse onto the original row.

Indexes are partial (`WHERE content_hash IS NOT NULL`) so rows that
somehow end up with NULL hashes don't blow up unique enforcement — the
backfill below populates every existing row, but the partial predicate
keeps the contract honest if the column ever drifts.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("content_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column("content_hash", sa.String(length=64), nullable=True),
    )

    # Backfill from raw_text. Single statement per table — sha256(text)
    # via pgcrypto-free SQL using `encode(digest(...), 'hex')` would
    # need an extension we don't ship; do it from Python instead so it
    # runs on any deployment.
    bind = op.get_bind()
    _backfill(bind, "documents")
    _backfill(bind, "jobs")

    op.create_index(
        "uq_documents_user_kind_content_hash",
        "documents",
        ["user_id", "kind", "content_hash"],
        unique=True,
        postgresql_where=sa.text("content_hash IS NOT NULL"),
        sqlite_where=sa.text("content_hash IS NOT NULL"),
    )
    op.create_index(
        "uq_jobs_user_content_hash",
        "jobs",
        ["user_id", "content_hash"],
        unique=True,
        postgresql_where=sa.text("content_hash IS NOT NULL"),
        sqlite_where=sa.text("content_hash IS NOT NULL"),
    )


def _backfill(bind: sa.engine.Connection, table: str) -> None:
    import hashlib

    rows = bind.execute(sa.text(f"SELECT id, raw_text FROM {table}")).fetchall()
    for row in rows:
        digest = hashlib.sha256((row.raw_text or "").encode("utf-8")).hexdigest()
        bind.execute(
            sa.text(f"UPDATE {table} SET content_hash = :h WHERE id = :i"),
            {"h": digest, "i": row.id},
        )


def downgrade() -> None:
    op.drop_index("uq_jobs_user_content_hash", table_name="jobs")
    op.drop_index("uq_documents_user_kind_content_hash", table_name="documents")
    op.drop_column("jobs", "content_hash")
    op.drop_column("documents", "content_hash")
