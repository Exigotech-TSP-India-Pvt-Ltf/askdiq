"""add content hashes for incremental ingestion

Revision ID: a1b2c3d4e5f6
Revises: 5491a7bdb113
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "5491a7bdb113"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Must match PIPELINE_VERSION in scripts/ingest_real_docs.py, because the
# backfill below hashes exactly the way the script does: sha256(version + content).
PIPELINE_VERSION = "v1"


def upgrade() -> None:
    op.add_column("documents", sa.Column("content_hash", sa.String(64), nullable=True))
    op.add_column("chunks", sa.Column("content_hash", sa.String(64), nullable=True))

    # Backfill existing chunks so the first incremental run does NOT
    # re-embed everything. Document hashes stay NULL on purpose: the first
    # run re-chunks each doc (free), finds every chunk hash already present,
    # and makes zero embedding calls.
    op.execute(
        f"""
        UPDATE chunks
        SET content_hash = encode(
            sha256(convert_to('{PIPELINE_VERSION}' || content, 'UTF8')), 'hex'
        )
        """
    )

    op.create_index(
        "ix_chunks_document_hash", "chunks", ["document_id", "content_hash"]
    )


def downgrade() -> None:
    op.drop_index("ix_chunks_document_hash", table_name="chunks")
    op.drop_column("chunks", "content_hash")
    op.drop_column("documents", "content_hash")