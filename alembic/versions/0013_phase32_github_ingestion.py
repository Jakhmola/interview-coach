"""phase 32: github ingestion — repos as grounded documents

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-30

Repos are ingested as ``documents`` rows with ``kind='github_repo'`` and
chunked into ``grounding_chunks`` with ``source_doc_kind='github_repo'``,
reusing the whole grounding pipeline. This migration:

* loosens ``ck_documents_kind`` and ``ck_grounding_chunks_source_doc_kind``
  to admit the new ``github_repo`` value (additive — no data rewrite);
* adds ``documents.source_url`` (the repo URL = a github_repo doc's identity)
  plus a partial unique index ``uq_documents_user_github_url`` for upsert;
* adds ``users.github_handle`` to persist the verified handle from the
  wizard card (one handle per user, survives across jobs).

All new columns are nullable and the CHECK loosening is additive, so this is
low-risk on a populated DB.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # users.github_handle
    op.add_column("users", sa.Column("github_handle", sa.String(length=39), nullable=True))

    # documents.source_url + partial unique index for repo identity
    op.add_column("documents", sa.Column("source_url", sa.String(length=2048), nullable=True))
    op.create_index(
        "uq_documents_user_github_url",
        "documents",
        ["user_id", "source_url"],
        unique=True,
        postgresql_where=sa.text("kind = 'github_repo'"),
    )

    # Loosen the two CHECK constraints to admit 'github_repo'.
    op.drop_constraint("ck_documents_kind", "documents", type_="check")
    op.create_check_constraint(
        "ck_documents_kind",
        "documents",
        "kind in ('cv', 'project_doc', 'github_repo')",
    )
    op.drop_constraint("ck_grounding_chunks_source_doc_kind", "grounding_chunks", type_="check")
    op.create_check_constraint(
        "ck_grounding_chunks_source_doc_kind",
        "grounding_chunks",
        "source_doc_kind in ('cv','project_doc','github_repo')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_grounding_chunks_source_doc_kind", "grounding_chunks", type_="check")
    op.create_check_constraint(
        "ck_grounding_chunks_source_doc_kind",
        "grounding_chunks",
        "source_doc_kind in ('cv','project_doc')",
    )
    op.drop_constraint("ck_documents_kind", "documents", type_="check")
    op.create_check_constraint(
        "ck_documents_kind",
        "documents",
        "kind in ('cv', 'project_doc')",
    )

    op.drop_index("uq_documents_user_github_url", table_name="documents")
    op.drop_column("documents", "source_url")
    op.drop_column("users", "github_handle")
