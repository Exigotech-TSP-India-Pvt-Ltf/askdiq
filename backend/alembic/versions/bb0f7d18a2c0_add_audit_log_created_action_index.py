"""add audit_log (created_at, action) index declared by the model

Revision ID: bb0f7d18a2c0
Revises: b2d6e08d82b3
Create Date: 2026-10-16 19:30:00.000000

``AuditLog.__table_args__`` declares ``ix_audit_log_created_action`` but no
migration ever created it, so databases built with ``alembic upgrade head``
(production) lacked an index that ``Base.metadata.create_all`` (tests) had,
and ``alembic revision --autogenerate`` kept proposing it. This closes that
drift.

``audit_log`` is append-only and grows without bound, so the index is built
``CONCURRENTLY`` inside Alembic's ``autocommit_block()`` (no write lock on a
live table); ``IF NOT EXISTS`` keeps a retry, or a database created from the
models, safe.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bb0f7d18a2c0"
down_revision: str | None = "b2d6e08d82b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "ix_audit_log_created_action"
TABLE = "audit_log"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            INDEX,
            TABLE,
            ["created_at", "action"],
            unique=False,
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            INDEX,
            table_name=TABLE,
            postgresql_concurrently=True,
            if_exists=True,
        )
