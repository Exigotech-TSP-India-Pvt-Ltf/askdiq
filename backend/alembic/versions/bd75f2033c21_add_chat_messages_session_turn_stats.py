"""add ndistinct statistics on chat_messages (session_id, turn_index)

Revision ID: bd75f2033c21
Revises: 4c1e9a7d2b65
Create Date: 2026-10-16 09:00:00.000000

The insights queries join each labelled assistant reply back to its user turn
on ``(session_id, turn_index)``. Without multi-column statistics the planner
multiplies the per-column distinct counts, expects the pair to repeat, and
picks a memoized nested loop with a 0% cache hit rate. That plan is about twice
as slow as a hash join at 100k turns. ``ndistinct`` statistics give it the true
pair count, so it picks the hash join reliably.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bd75f2033c21"
down_revision: str | None = "4c1e9a7d2b65"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATISTICS = "stx_chat_messages_session_turn"


def upgrade() -> None:
    op.execute(
        f"CREATE STATISTICS IF NOT EXISTS {STATISTICS} (ndistinct) "
        "ON session_id, turn_index FROM chat_messages"
    )
    # Build them now instead of waiting for the next autovacuum analyze.
    op.execute("ANALYZE chat_messages")


def downgrade() -> None:
    op.execute(f"DROP STATISTICS IF EXISTS {STATISTICS}")
