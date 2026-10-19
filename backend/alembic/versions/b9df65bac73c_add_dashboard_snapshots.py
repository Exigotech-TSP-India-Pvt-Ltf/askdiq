"""add dashboard_snapshots (precomputed dashboard aggregates)

Revision ID: b9df65bac73c
Revises: bcecf2e175fd
Create Date: 2026-10-19 17:00:00.000000

One row per dashboard aggregate (API payload) per kind + parameters. The
snapshot refresher (app.dashboard.snapshots) keeps the requested ones fresh
in the background under an advisory lock; dashboard requests read a row
instead of aggregating the history. A new, empty table: no backfill here
(the refresher computes the defaults on its first run).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b9df65bac73c"
down_revision: str | None = "bcecf2e175fd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dashboard_snapshots",
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("params", postgresql.JSONB(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("compute_ms", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_dashboard_snapshots_requested_at",
        "dashboard_snapshots",
        ["requested_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_dashboard_snapshots_requested_at", table_name="dashboard_snapshots"
    )
    op.drop_table("dashboard_snapshots")
