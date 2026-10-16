"""add chat_messages newest-user-turn partial index

Revision ID: b2d6e08d82b3
Revises: 254857d10a20
Create Date: 2026-10-16 18:00:00.000000

``GET /admin/usage/queries`` (Usage & Cost overview and Queries tabs) pages
user turns newest first: ``ORDER BY created_at DESC NULLS LAST ... LIMIT n``
over ``role = 'user'``. The plain ``ix_chat_messages_created_at`` (ascending,
nulls last) cannot return that order, so every page sorted all user turns
(EXPLAIN ANALYZE on ~16k user turns: 3.9 s with a parallel sort, 58 ms with
this index walking the first ``n`` rows).

``chat_messages`` grows without bound, so the index is built
``CONCURRENTLY`` inside Alembic's ``autocommit_block()`` (no write lock on a
live table); ``IF NOT EXISTS`` makes a retry after an interrupted build safe.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2d6e08d82b3"
down_revision: str | None = "254857d10a20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "ix_chat_messages_user_created_desc"
TABLE = "chat_messages"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            INDEX,
            TABLE,
            [sa.text("created_at DESC NULLS LAST")],
            unique=False,
            postgresql_where=sa.text("role = 'user'"),
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
