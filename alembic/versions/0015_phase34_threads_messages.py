"""phase 34: conversational interviewer — turns → threads + messages

Revision ID: 0015
Revises: 0014
Create Date: 2026-06-03

Phase 34 makes a topic a **thread** (a root question plus the interviewer's
follow-up moves and the candidate's answers, evaluated once at close) and an
utterance a **message**. This is a **clean break** — pre-34 ``turns`` data is
dev-stage throwaway, so the migration drops ``turns`` and creates ``threads``
+ ``messages`` from scratch (no backfill). ``sessions`` is unchanged.

The downgrade drops the two new tables and recreates the ``turns`` table as it
stood after 0006 (empty — the data is gone). Verify down/up/down on live
Postgres; ``make test`` won't catch ordering/FK issues (it uses create_all,
not Alembic).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Clean break: drop the per-turn table (and its index). No backfill.
    op.drop_index("ix_turns_session_id", table_name="turns")
    op.drop_table("turns")

    op.create_table(
        "threads",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("thread_index", sa.Integer(), nullable=False),
        sa.Column("focus_key", sa.String(length=256), nullable=True),
        sa.Column("focus_label", sa.Text(), nullable=True),
        sa.Column("focus_document_ids", postgresql.JSONB(), nullable=True),
        sa.Column("anchors_json", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column("model_answer", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            ondelete="CASCADE",
            name="fk_threads_session_id",
        ),
        sa.CheckConstraint("status in ('open','closed')", name="ck_threads_status"),
        sa.UniqueConstraint("session_id", "thread_index", name="uq_threads_session_thread"),
    )
    op.create_index("ix_threads_session_id", "threads", ["session_id"])

    op.create_table(
        "messages",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["threads.id"],
            ondelete="CASCADE",
            name="fk_messages_thread_id",
        ),
        sa.CheckConstraint("role in ('interviewer','candidate')", name="ck_messages_role"),
        sa.CheckConstraint(
            "kind is null or kind in ('question','probe','clarify','nudge')",
            name="ck_messages_kind",
        ),
        sa.UniqueConstraint("thread_id", "seq", name="uq_messages_thread_seq"),
    )
    op.create_index("ix_messages_thread_id", "messages", ["thread_id"])


def downgrade() -> None:
    op.drop_index("ix_messages_thread_id", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_threads_session_id", table_name="threads")
    op.drop_table("threads")

    # Recreate the turns table as it stood after 0006 (empty — the clean break
    # discarded its rows). Mirrors 0006's create_table exactly.
    op.create_table(
        "turns",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("turn_index", sa.Integer(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("anchors_json", postgresql.JSONB(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column("model_answer", sa.Text(), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            ondelete="CASCADE",
            name="fk_turns_session_id",
        ),
        sa.UniqueConstraint("session_id", "turn_index", name="uq_turns_session_turn"),
    )
    op.create_index("ix_turns_session_id", "turns", ["session_id"])
