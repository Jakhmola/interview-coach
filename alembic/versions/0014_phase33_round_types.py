"""phase 33: round-type system — rename resume_walkthrough → experience_deep_dive

Revision ID: 0014
Revises: 0013
Create Date: 2026-06-02

Phase 33 turns the two-value round type into a three-round system. This
migration:

* renames existing ``resume_walkthrough`` sessions to ``experience_deep_dive``
  (the round absorbed the would-be github round — same focus, repo-grounded);
* swaps ``ck_sessions_round_type`` to admit the three new values
  (``experience_deep_dive``, ``technical_challenge``, ``behavioral_star``).

The downgrade maps ``experience_deep_dive`` back to ``resume_walkthrough`` and
restores the old two-value CHECK. It is **one-way for ``technical_challenge``
rows**: that round did not exist before this phase, so recreating the old
constraint will (correctly) fail if any such session was created — delete or
reassign those rows before downgrading.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop the old CHECK *before* the UPDATE: it admits only the two pre-phase
    # values, so writing 'experience_deep_dive' while it's still in place fails
    # with a CheckViolation. Order is drop → update → recreate.
    op.drop_constraint("ck_sessions_round_type", "sessions", type_="check")
    op.execute(
        "UPDATE sessions SET round_type='experience_deep_dive' "
        "WHERE round_type='resume_walkthrough'"
    )
    op.create_check_constraint(
        "ck_sessions_round_type",
        "sessions",
        "round_type in ('experience_deep_dive','technical_challenge','behavioral_star')",
    )


def downgrade() -> None:
    # Mirror of upgrade: drop the new CHECK before the reverse UPDATE, since the
    # three-value constraint forbids 'resume_walkthrough'.
    op.drop_constraint("ck_sessions_round_type", "sessions", type_="check")
    op.execute(
        "UPDATE sessions SET round_type='resume_walkthrough' "
        "WHERE round_type='experience_deep_dive'"
    )
    # Recreating the old two-value CHECK fails if any technical_challenge rows
    # remain — that round is new in Phase 33 and has no pre-image. One-way.
    op.create_check_constraint(
        "ck_sessions_round_type",
        "sessions",
        "round_type in ('resume_walkthrough','behavioral_star')",
    )
