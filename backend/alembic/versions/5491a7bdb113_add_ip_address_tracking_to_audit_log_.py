"""add ip address tracking to audit log chat sessions and messages

Revision ID: 5491a7bdb113
Revises: 6264fc8f10c0
Create Date: 2026-09-29 12:54:43.257110

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "5491a7bdb113"
down_revision: Union[str, None] = "6264fc8f10c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("audit_log", sa.Column("ip_address", sa.String(64), nullable=True))
    op.add_column(
        "chat_sessions", sa.Column("created_ip", sa.String(64), nullable=True)
    )
    op.add_column(
        "chat_messages", sa.Column("ip_address", sa.String(64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("chat_messages", "ip_address")
    op.drop_column("chat_sessions", "created_ip")
    op.drop_column("audit_log", "ip_address")
