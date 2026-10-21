"""add documents.source_url / section_links and knowledge_settings (OR-7)

Revision ID: 05b32ff49272
Revises: 041c4335565e
Create Date: 2026-10-21 12:00:00.000000

Knowledge source URLs: each document may carry the public https page it
comes from (``source_url``, nullable: nothing is backfilled) and opt in to
per-section anchors (``section_links``, off by default). Chunks never copy
the URL, so editing it never re-embeds. ``knowledge_settings`` is the
singleton link policy behind Settings > Knowledge links (host allow-list,
"require a source URL on upload"); no seed row, the defaults apply until
the first save.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "05b32ff49272"
down_revision: str | None = "041c4335565e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents", sa.Column("source_url", sa.String(2048), nullable=True)
    )
    op.add_column(
        "documents",
        sa.Column(
            "section_links", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.create_table(
        "knowledge_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "allowed_hosts",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column(
            "require_source_url",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("updated_by", sa.String(320), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint("id = 1", name="ck_knowledge_settings_singleton"),
    )


def downgrade() -> None:
    op.drop_table("knowledge_settings")
    op.drop_column("documents", "section_links")
    op.drop_column("documents", "source_url")
