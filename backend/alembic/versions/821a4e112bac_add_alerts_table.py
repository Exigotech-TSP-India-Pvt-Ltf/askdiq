"""add alerts (rule-raised alerts with dedupe, lifecycle and email delivery state)

Revision ID: 821a4e112bac
Revises: b229b53f87c8
Create Date: 2026-10-19 16:00:00.000000

One active (open / acknowledged) row per ``dedupe_key`` is enforced by a
partial unique index, so concurrent workers raising the same condition
coalesce into one alert.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "821a4e112bac"
down_revision: str | None = "b229b53f87c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=None if nullable else sa.func.now(),
    )


def upgrade() -> None:
    op.create_table(
        "alerts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("rule", sa.String(64), nullable=False),
        sa.Column("dedupe_key", sa.String(200), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("component", sa.String(64), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("occurrences", sa.Integer(), nullable=False, server_default="1"),
        _ts("first_seen_at"),
        _ts("last_seen_at"),
        _ts("acknowledged_at", nullable=True),
        sa.Column("acknowledged_by", sa.String(320), nullable=True),
        _ts("resolved_at", nullable=True),
        sa.Column("resolved_by", sa.String(320), nullable=True),
        sa.Column(
            "notify_status", sa.String(16), nullable=False, server_default="pending"
        ),
        sa.Column("notify_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notify_error", sa.Text(), nullable=True),
        _ts("notified_at", nullable=True),
        _ts("resolved_notified_at", nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'critical')", name="ck_alerts_severity"
        ),
        sa.CheckConstraint(
            "status IN ('open', 'acknowledged', 'resolved')", name="ck_alerts_status"
        ),
        sa.CheckConstraint(
            "notify_status IN ('pending', 'queued', 'sent', 'retrying', 'failed', "
            "'disabled', 'skipped', 'suppressed')",
            name="ck_alerts_notify_status",
        ),
    )
    op.create_index(
        "uq_alerts_active_dedupe_key",
        "alerts",
        ["dedupe_key"],
        unique=True,
        postgresql_where=sa.text("status IN ('open', 'acknowledged')"),
    )
    op.create_index(
        "ix_alerts_status_last_seen", "alerts", ["status", "last_seen_at"]
    )
    op.create_index(
        "ix_alerts_dedupe_key_notified", "alerts", ["dedupe_key", "notified_at"]
    )
    op.create_index("ix_alerts_created_id", "alerts", ["created_at", "id"])


def downgrade() -> None:
    op.drop_index("ix_alerts_created_id", table_name="alerts")
    op.drop_index("ix_alerts_dedupe_key_notified", table_name="alerts")
    op.drop_index("ix_alerts_status_last_seen", table_name="alerts")
    op.drop_index("uq_alerts_active_dedupe_key", table_name="alerts")
    op.drop_table("alerts")
