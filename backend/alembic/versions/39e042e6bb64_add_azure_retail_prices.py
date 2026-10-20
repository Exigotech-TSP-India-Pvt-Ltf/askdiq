"""add azure_retail_prices and model_settings price provenance

Revision ID: 39e042e6bb64
Revises: ca723897761c
Create Date: 2026-10-20 21:00:00.000000

Azure Insights Phase 1: the public Azure Retail Prices API rows for our
models (USD + the pricing currency), one row per meter, currency and
effective date (history kept), and where the saved model prices came from
(``manual`` or ``azure_retail``) with their effective / sync dates.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "39e042e6bb64"
down_revision: str | None = "ca723897761c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "azure_retail_prices"
INDEX = "ix_azure_retail_prices_lookup"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("meter_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("effective_start_date", sa.Date(), nullable=False),
        sa.Column("meter_name", sa.String(200), nullable=False),
        sa.Column("product_name", sa.String(200), nullable=True),
        sa.Column("sku_name", sa.String(200), nullable=True),
        sa.Column("service_name", sa.String(100), nullable=True),
        sa.Column("arm_region_name", sa.String(40), nullable=False),
        sa.Column("unit_of_measure", sa.String(20), nullable=False),
        sa.Column("retail_price", sa.Numeric(18, 10), nullable=False),
        sa.Column("price_per_million", sa.Numeric(18, 6), nullable=True),
        sa.Column("role", sa.String(16), nullable=True),
        sa.Column("model_key", sa.String(64), nullable=True),
        sa.Column("deployment_type", sa.String(16), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint(
            "meter_id", "currency", "effective_start_date", name="pk_azure_retail_prices"
        ),
        sa.CheckConstraint("retail_price >= 0", name="ck_azure_retail_prices_price"),
    )
    op.create_index(
        INDEX,
        TABLE,
        ["arm_region_name", "meter_name", "currency", "effective_start_date"],
    )
    op.add_column("model_settings", sa.Column("price_source", sa.String(16)))
    op.add_column(
        "model_settings",
        sa.Column("prices_synced_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "model_settings", sa.Column("prices_effective_date", sa.Date(), nullable=True)
    )
    op.create_check_constraint(
        "ck_model_settings_price_source",
        "model_settings",
        "price_source IS NULL OR price_source IN ('manual', 'azure_retail')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_model_settings_price_source", "model_settings", type_="check")
    op.drop_column("model_settings", "prices_effective_date")
    op.drop_column("model_settings", "prices_synced_at")
    op.drop_column("model_settings", "price_source")
    op.drop_index(INDEX, table_name=TABLE)
    op.drop_table(TABLE)
