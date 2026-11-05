"""add chunk search indexes: full-text GIN and HNSW vector (cosine)

Revision ID: a1b2c3d4e5f7
Revises: 05b32ff49272
Create Date: 2026-11-05 11:30:00.000000

Retrieval runs two searches over ``chunks`` (app.retrieval.vector_store):
cosine similarity on ``embedding`` and Postgres full-text search on
``content``. Without indexes both scan every chunk. The GIN index matches
the query's expression exactly (``to_tsvector('english', content)``), so
the planner can use it; HNSW with ``vector_cosine_ops`` serves the
``<=>`` cosine-distance ordering (pgvector >= 0.5).
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f7"
down_revision: str | None = "05b32ff49272"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chunks_content_fts ON chunks "
        "USING gin (to_tsvector('english', content))"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chunks_embedding_hnsw ON chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_chunks_content_fts")
