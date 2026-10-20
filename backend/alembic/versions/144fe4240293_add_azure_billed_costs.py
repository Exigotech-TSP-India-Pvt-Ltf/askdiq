"""add azure_billed_costs (Azure Cost Management billed cost per day)

Revision ID: 144fe4240293
Revises: 6e0a31f87ff3
Create Date: 2026-10-20 23:00:00.000000

Azure Insights Phase 3: the billed cost from the Cost Management Query API
per UTC usage day, meter and (lower-cased) resource id, in the billing
currency. Re-read days are upserted (Azure rerates until month close);
``is_final`` once the month closed + 3 days. History kept 400+ days.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "144fe4240293"
down_revision: str | None = "6e0a31f87ff3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "azure_billed_costs"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("meter", sa.String(200), nullable=False),
        sa.Column("resource_id", sa.String(512), nullable=False),
        sa.Column("cost", sa.Numeric(20, 8), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("is_final", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint(
            "usage_date", "meter", "resource_id", name="pk_azure_billed_costs"
        ),
    )


def downgrade() -> None:
    op.drop_table(TABLE)
