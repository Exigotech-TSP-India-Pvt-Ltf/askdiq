"""add model_settings (runtime LLM / embedding model and pricing overrides)

Revision ID: c3d4e5f6a7b8
Revises: bb0f7d18a2c0
Create Date: 2026-10-19 10:00:00.000000

Singleton row (``id = 1``) behind Settings > Models. Every override column is
nullable: null means "use the .env value". No seed row: it is created on the
first save.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "bb0f7d18a2c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PRICES = (
    "price_prompt_per_million",
    "price_cached_per_million",
    "price_output_per_million",
    "price_embed_per_million",
)


def upgrade() -> None:
    op.create_table(
        "model_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("llm_deployment", sa.String(64), nullable=True),
        sa.Column("embedding_model", sa.String(64), nullable=True),
        *(sa.Column(name, sa.Float(), nullable=True) for name in _PRICES),
        sa.Column("updated_by", sa.String(320), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint("id = 1", name="ck_model_settings_singleton"),
        sa.CheckConstraint(
            " AND ".join(f"coalesce({name}, 0) >= 0" for name in _PRICES),
            name="ck_model_settings_prices",
        ),
    )


def downgrade() -> None:
    op.drop_table("model_settings")
