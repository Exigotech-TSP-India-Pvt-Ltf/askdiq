"""add azure_metric_points (Azure Monitor platform metrics, Phase 2)

Revision ID: a788c15bf749
Revises: ca723897761c
Create Date: 2026-10-20 23:00:00.000000

One row per (deployment, metric, status_code, grain, bucket start) of the
Foundry / Azure OpenAI account's free platform metrics: tokens, requests by
HTTP status, latency and availability, and the Preview estimated cost.
PT1M rows are kept 7 days and PT1H rows 93 days (pruned by the poll job).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a788c15bf749"
down_revision: str | None = "ca723897761c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "azure_metric_points"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("deployment", sa.String(128), primary_key=True),
        sa.Column("metric", sa.String(64), primary_key=True),
        sa.Column("status_code", sa.String(16), primary_key=True, server_default=""),
        sa.Column("grain", sa.String(8), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("total", sa.Float(), nullable=True),
        sa.Column("average", sa.Float(), nullable=True),
        sa.Column("maximum", sa.Float(), nullable=True),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "grain IN ('PT1M', 'PT1H')", name="ck_azure_metric_points_grain"
        ),
    )
    op.create_index("ix_azure_metric_points_grain_ts", TABLE, ["grain", "ts"])
    op.create_index(
        "ix_azure_metric_points_metric_grain_ts", TABLE, ["metric", "grain", "ts"]
    )


def downgrade() -> None:
    op.drop_index("ix_azure_metric_points_metric_grain_ts", table_name=TABLE)
    op.drop_index("ix_azure_metric_points_grain_ts", table_name=TABLE)
    op.drop_table(TABLE)
