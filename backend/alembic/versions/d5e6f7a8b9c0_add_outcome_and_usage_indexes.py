"""add outcome and usage indexes

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-10-13 14:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "d5e6f7a8b9c0"
down_revision: str | None = "c4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add outcome column to chat_messages
    op.add_column("chat_messages", sa.Column("outcome", sa.String(length=24), nullable=True))
    op.create_index(
        "ix_chat_messages_outcome",
        "chat_messages",
        ["outcome"],
        unique=False,
    )

    # 2. Add composite index on chat_messages(ip_address, created_at)
    op.create_index(
        "ix_chat_messages_ip_created",
        "chat_messages",
        ["ip_address", "created_at"],
        unique=False,
    )

    # 3. Add index on latency_metrics(request_id)
    op.create_index(
        "ix_latency_metrics_request_id",
        "latency_metrics",
        ["request_id"],
        unique=False,
    )

    # 4. Add composite index on audit_log(ip_address, created_at)
    op.create_index(
        "ix_audit_log_ip_created",
        "audit_log",
        ["ip_address", "created_at"],
        unique=False,
    )

    # 5. Add index on chat_sessions(created_ip)
    op.create_index(
        "ix_chat_sessions_created_ip",
        "chat_sessions",
        ["created_ip"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_chat_sessions_created_ip", table_name="chat_sessions")
    op.drop_index("ix_audit_log_ip_created", table_name="audit_log")
    op.drop_index("ix_latency_metrics_request_id", table_name="latency_metrics")
    op.drop_index("ix_chat_messages_ip_created", table_name="chat_messages")
    op.drop_index("ix_chat_messages_outcome", table_name="chat_messages")
    op.drop_column("chat_messages", "outcome")
