"""add chat_sessions (user_email, created_ip, last_activity_at) indexes

Revision ID: bcecf2e175fd
Revises: 821a4e112bac
Create Date: 2026-10-19 20:00:00.000000

Every POST /query without a session_id looks up the actor's newest session
for its IP, and GET /sessions lists an actor's sessions newest first. With
only single-column indexes both sort all of the actor's sessions (the shared
website visitor actor can own thousands). Built CONCURRENTLY so a deploy never
blocks chat writes.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bcecf2e175fd"
down_revision: str | None = "821a4e112bac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES = {
    "ix_chat_sessions_user_ip_activity": ["user_email", "created_ip", "last_activity_at"],
    "ix_chat_sessions_user_activity": ["user_email", "last_activity_at"],
}


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for name, columns in _INDEXES.items():
            op.create_index(
                name,
                "chat_sessions",
                columns,
                postgresql_concurrently=True,
                if_not_exists=True,
            )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name in _INDEXES:
            op.drop_index(
                name,
                table_name="chat_sessions",
                postgresql_concurrently=True,
                if_exists=True,
            )
