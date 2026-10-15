"""add brand_settings singleton and content-addressed brand_assets

Revision ID: b0c1d2e3f4a5
Revises: 4c1e9a7d2b65
Create Date: 2026-10-15 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b0c1d2e3f4a5"
down_revision: str | None = "4c1e9a7d2b65"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TZ = sa.DateTime(timezone=True)
_HEX = "'^#[0-9a-f]{6}$'"

# Frozen copy of app/branding/defaults.py at the time of this migration
# (config defaults + Exigo Tech teal + DeployIQ login copy).
SEED = {
    "id": 1,
    "product_name": "DeployIQ",
    "assistant_name": "AskDIQ",
    "company_name": "Exigo Tech",
    "support_email": "support@deployiq.com",
    "access_contact_email": None,
    "primary_hex": "#008080",
    "accent_hex": "#00584f",
    "tint_hex": "#eaf6f5",
    "login_eyebrow": "ASKDIQ",
    "login_headline": "Answers you can trust, grounded in your own knowledge base.",
    "login_subtext": (
        "AskDIQ searches approved DeployIQ documentation and cites every source, "
        "so your team can move fast without guessing."
    ),
    "login_description": (
        "Sign in with your Microsoft work account to open the DeployIQ admin "
        "console and assistant."
    ),
    "privacy_url": None,
    "terms_url": None,
    "help_url": None,
    "chat_sample_questions": [
        "What is the Essential Eight and how does DeployIQ implement it?",
        "How does DeployIQ keep humans in the loop over AI actions?",
        "What does the Zero Trust Professional plan include?",
        "How does DeployIQ handle customer data?",
    ],
    "version": 1,
}

_ASSET_COLUMNS = (
    "logo_light_asset_id",
    "logo_dark_asset_id",
    "mark_light_asset_id",
    "mark_dark_asset_id",
    "favicon_asset_id",
)


def upgrade() -> None:
    op.create_table(
        "brand_assets",
        sa.Column("sha256", sa.String(64), primary_key=True),
        sa.Column("mime", sa.String(32), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(320), nullable=True),
        sa.Column("created_at", _TZ, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "mime IN ('image/png', 'image/webp', 'image/svg+xml')",
            name="ck_brand_assets_mime",
        ),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_brand_assets_sha256"),
    )

    asset_columns = [
        sa.Column(
            name,
            sa.String(64),
            sa.ForeignKey("brand_assets.sha256", ondelete="SET NULL"),
            nullable=True,
        )
        for name in _ASSET_COLUMNS
    ]
    brand_settings = op.create_table(
        "brand_settings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("product_name", sa.String(80), nullable=False),
        sa.Column("assistant_name", sa.String(80), nullable=False),
        sa.Column("company_name", sa.String(120), nullable=False),
        sa.Column("support_email", sa.String(320), nullable=False),
        sa.Column("access_contact_email", sa.String(320), nullable=True),
        sa.Column("primary_hex", sa.String(7), nullable=False),
        sa.Column("accent_hex", sa.String(7), nullable=True),
        sa.Column("tint_hex", sa.String(7), nullable=True),
        *asset_columns,
        sa.Column("login_eyebrow", sa.String(60), nullable=True),
        sa.Column("login_headline", sa.String(160), nullable=True),
        sa.Column("login_subtext", sa.String(400), nullable=True),
        sa.Column("login_description", sa.String(1000), nullable=True),
        sa.Column("privacy_url", sa.String(2048), nullable=True),
        sa.Column("terms_url", sa.String(2048), nullable=True),
        sa.Column("help_url", sa.String(2048), nullable=True),
        sa.Column(
            "chat_sample_questions",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column("updated_by", sa.String(320), nullable=True),
        sa.Column("updated_at", _TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint("id = 1", name="ck_brand_settings_singleton"),
        sa.CheckConstraint(
            f"primary_hex ~ {_HEX} "
            f"AND (accent_hex IS NULL OR accent_hex ~ {_HEX}) "
            f"AND (tint_hex IS NULL OR tint_hex ~ {_HEX})",
            name="ck_brand_settings_hex",
        ),
    )
    op.bulk_insert(brand_settings, [SEED])


def downgrade() -> None:
    op.drop_table("brand_settings")
    op.drop_table("brand_assets")
