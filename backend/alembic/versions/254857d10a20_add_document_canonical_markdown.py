"""add documents.canonical_markdown

Revision ID: 254857d10a20
Revises: 72125f44984d
Create Date: 2026-10-16 09:00:00.000000

Every upload (.md or .docx) is standardized to one canonical Markdown text
before chunking, but only the chunks were stored, so the Documents page could
not show the standardized file. This adds a nullable ``TEXT`` column holding
that canonical Markdown (bounded by ``INGEST_MAX_CHARS``). Rows ingested
before this migration keep ``NULL`` until the document is uploaded again.
Adding a nullable column without a default is a metadata-only change.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "254857d10a20"
down_revision: str | None = "72125f44984d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents", sa.Column("canonical_markdown", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("documents", "canonical_markdown")
