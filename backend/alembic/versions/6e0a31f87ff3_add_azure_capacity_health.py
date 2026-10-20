"""add Azure capacity, quota and health tables (Azure Insights Phase 4)

Revision ID: 6e0a31f87ff3
Revises: ca723897761c
Create Date: 2026-10-20 23:00:00.000000

History is kept: deployments and regional quota per poll (``fetched_at``),
Resource Health one row per status change, Service Health events upserted
by tracking id, Advisor recommendations upserted by id (resolved when gone).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6e0a31f87ff3"
down_revision: str | None = "ca723897761c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts(name: str, *, nullable: bool = True, primary_key: bool = False) -> sa.Column:
    return sa.Column(
        name, sa.DateTime(timezone=True), nullable=nullable, primary_key=primary_key
    )


def upgrade() -> None:
    op.create_table(
        "azure_deployments",
        sa.Column("name", sa.String(128), primary_key=True),
        _ts("fetched_at", nullable=False, primary_key=True),
        sa.Column("model_name", sa.String(128)),
        sa.Column("model_version", sa.String(64)),
        sa.Column("sku_name", sa.String(64)),
        sa.Column("sku_capacity", sa.Integer()),
        sa.Column("current_capacity", sa.Integer()),
        sa.Column("tpm_limit", sa.BigInteger()),
        sa.Column("rpm_limit", sa.BigInteger()),
        sa.Column("rate_limits", postgresql.JSONB()),
        sa.Column("provisioning_state", sa.String(32)),
    )
    op.create_index(
        "ix_azure_deployments_fetched_at", "azure_deployments", ["fetched_at"]
    )
    op.create_table(
        "azure_quota_usage",
        sa.Column("location", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(160), primary_key=True),
        _ts("fetched_at", nullable=False, primary_key=True),
        sa.Column("localized_name", sa.String(200)),
        sa.Column("current_value", sa.Float()),
        sa.Column("limit_value", sa.Float()),
        sa.Column("unit", sa.String(32)),
        sa.Column("status", sa.String(32)),
    )
    op.create_index(
        "ix_azure_quota_usage_fetched_at", "azure_quota_usage", ["fetched_at"]
    )
    op.create_table(
        "azure_health_status",
        sa.Column("resource_id", sa.String(400), primary_key=True),
        _ts("started_at", nullable=False, primary_key=True),
        sa.Column("availability_state", sa.String(24), nullable=False),
        sa.Column("summary", sa.Text()),
        sa.Column("reason_type", sa.String(64)),
        _ts("occurred_at"),
        _ts("reported_at"),
        _ts("resolution_eta"),
        _ts("checked_at", nullable=False),
        _ts("ended_at"),
    )
    op.create_index(
        "ix_azure_health_status_started_at", "azure_health_status", ["started_at"]
    )
    op.create_table(
        "azure_service_events",
        sa.Column("tracking_id", sa.String(64), primary_key=True),
        sa.Column("event_type", sa.String(48)),
        sa.Column("status", sa.String(24)),
        sa.Column("level", sa.String(24)),
        sa.Column("title", sa.Text()),
        sa.Column("summary", sa.Text()),
        sa.Column("services", postgresql.JSONB()),
        sa.Column("regions", postgresql.JSONB()),
        _ts("impact_start"),
        _ts("impact_mitigation"),
        _ts("last_update"),
        _ts("first_seen_at", nullable=False),
        _ts("last_seen_at", nullable=False),
    )
    op.create_index(
        "ix_azure_service_events_last_seen", "azure_service_events", ["last_seen_at"]
    )
    op.create_table(
        "azure_advisor_recs",
        sa.Column("rec_id", sa.String(64), primary_key=True),
        sa.Column("category", sa.String(48)),
        sa.Column("impact", sa.String(16)),
        sa.Column("impacted_field", sa.String(200)),
        sa.Column("impacted_value", sa.String(200)),
        sa.Column("problem", sa.Text()),
        sa.Column("solution", sa.Text()),
        sa.Column("learn_more_link", sa.Text()),
        sa.Column("extended", postgresql.JSONB()),
        _ts("last_updated"),
        _ts("first_seen_at", nullable=False),
        _ts("last_seen_at", nullable=False),
        _ts("resolved_at"),
    )


def downgrade() -> None:
    op.drop_table("azure_advisor_recs")
    op.drop_index(
        "ix_azure_service_events_last_seen", table_name="azure_service_events"
    )
    op.drop_table("azure_service_events")
    op.drop_index("ix_azure_health_status_started_at", table_name="azure_health_status")
    op.drop_table("azure_health_status")
    op.drop_index("ix_azure_quota_usage_fetched_at", table_name="azure_quota_usage")
    op.drop_table("azure_quota_usage")
    op.drop_index("ix_azure_deployments_fetched_at", table_name="azure_deployments")
    op.drop_table("azure_deployments")
