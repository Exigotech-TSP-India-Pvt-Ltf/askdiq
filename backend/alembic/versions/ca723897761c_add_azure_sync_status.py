"""add azure_sync_status (Azure Insights per-source status and call budget)

Revision ID: ca723897761c
Revises: 144787ba01b1
Create Date: 2026-10-20 20:00:00.000000

One row per Azure Insights source (retail, metrics, costs, capacity, health,
...): last run / success / sanitised error, consecutive failures, the next
scheduled run, and the monthly call counter behind the hard metrics cap.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ca723897761c"
down_revision: str | None = "144787ba01b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "azure_sync_status"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("source", sa.String(32), primary_key=True),
        sa.Column("state", sa.String(24), nullable=False, server_default="never_run"),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(80), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column(
            "consecutive_failures", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("calls_month", sa.Date(), nullable=True),
        sa.Column("calls_this_month", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "consecutive_failures >= 0 AND calls_this_month >= 0",
            name="ck_azure_sync_status_counts",
        ),
    )


def downgrade() -> None:
    op.drop_table(TABLE)
