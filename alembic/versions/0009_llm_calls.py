"""phase 16: llm_calls telemetry table

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-14

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_calls",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("node_name", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("error_class", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_llm_calls_ts", "llm_calls", [sa.text("ts DESC")])
    op.create_index("ix_llm_calls_node", "llm_calls", ["node_name", sa.text("ts DESC")])


def downgrade() -> None:
    op.drop_index("ix_llm_calls_node", table_name="llm_calls")
    op.drop_index("ix_llm_calls_ts", table_name="llm_calls")
    op.drop_table("llm_calls")
