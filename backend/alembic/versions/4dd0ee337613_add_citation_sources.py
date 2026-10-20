"""add citation_sources (expanded citation sources) and citation_sync_state

Revision ID: 4dd0ee337613
Revises: 10d09e060b7d
Create Date: 2026-10-20 11:00:00.000000

Citation stats (Knowledge page, document detail) expanded the JSON source
lists of every query-audit row and evaluation record in the range on each
read. ``citation_sources`` holds that expansion (insert-only, filled
incrementally by the jobs worker, parents' deletes cascade), so any date
range is an indexed aggregation. Reads add a live expansion of the rows newer
than ``citation_sync_state``, so results are unchanged. The backfill runs in
the worker, not here.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4dd0ee337613"
down_revision: str | None = "10d09e060b7d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "citation_sources",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "audit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("audit_log.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "eval_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("evaluation_records.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("cited_at", sa.DateTime(), nullable=False),
        sa.Column("chunk_ref", sa.Text(), nullable=True),
        sa.Column("doc_ref", sa.Text(), nullable=True),
        sa.Column("name_ref", sa.Text(), nullable=True),
    )
    op.create_index("ix_citation_sources_cited_at", "citation_sources", ["cited_at"])
    op.create_index(
        "uq_citation_sources_audit",
        "citation_sources",
        ["audit_id", "ordinal"],
        unique=True,
        postgresql_where=sa.text("audit_id IS NOT NULL"),
    )
    op.create_index(
        "uq_citation_sources_eval",
        "citation_sources",
        ["eval_id", "ordinal"],
        unique=True,
        postgresql_where=sa.text("eval_id IS NOT NULL"),
    )
    op.create_table(
        "citation_sync_state",
        sa.Column("origin", sa.String(16), primary_key=True),
        sa.Column("synced_until", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("citation_sync_state")
    op.drop_index("uq_citation_sources_eval", table_name="citation_sources")
    op.drop_index("uq_citation_sources_audit", table_name="citation_sources")
    op.drop_index("ix_citation_sources_cited_at", table_name="citation_sources")
    op.drop_table("citation_sources")
