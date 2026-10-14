"""add unique document source_name lower and updated_at

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-10-13 11:15:00.000000

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Pre-check for existing case-insensitive duplicate document source names
    bind = op.get_bind()
    dups = bind.execute(
        sa.text(
            "SELECT lower(source_name), count(*) "
            "FROM documents GROUP BY lower(source_name) HAVING count(*) > 1;"
        )
    ).fetchall()
    if dups:
        dup_names = [d[0] for d in dups]
        raise RuntimeError(
            f"Cannot create unique index: found case-insensitive duplicate documents in database: {dup_names}. "
            "Run scripts/reconcile_document_names.py to resolve collisions before applying migration."
        )

    # 2. Add updated_at column to documents if not already present
    op.execute(
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT now();"
    )

    # 3. Create unique index on lower(source_name)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_documents_source_name_lower ON documents (lower(source_name));"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_documents_source_name_lower;")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS updated_at;")
