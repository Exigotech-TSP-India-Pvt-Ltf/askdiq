"""add resource_samples (per-worker resource telemetry, short retention)

Revision ID: bd881e8aa812
Revises: bcecf2e175fd
Create Date: 2026-10-19 21:00:00.000000

One row per API worker every METRICS_SAMPLE_INTERVAL_SECONDS; pruned after
METRICS_HISTORY_MINUTES. Shared by every worker and replica for the
monitoring gauges, the live stream and the resource alert rules.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bd881e8aa812"
down_revision: str | None = "bcecf2e175fd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "resource_samples",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("worker_id", sa.String(128), nullable=False),
        sa.Column("hostname", sa.String(255), nullable=False),
        sa.Column("sampled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sample", postgresql.JSONB(), nullable=False),
    )
    op.create_index(
        "ix_resource_samples_sampled_at", "resource_samples", ["sampled_at"]
    )
    op.create_index(
        "ix_resource_samples_worker_sampled",
        "resource_samples",
        ["worker_id", "sampled_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_resource_samples_worker_sampled", table_name="resource_samples")
    op.drop_index("ix_resource_samples_sampled_at", table_name="resource_samples")
    op.drop_table("resource_samples")
