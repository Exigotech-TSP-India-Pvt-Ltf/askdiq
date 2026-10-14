"""add stage tokens and cost columns to chat_messages

Revision ID: b2c3d4e5f6a8
Revises: f6a7b8c9d0e1
Create Date: 2026-10-13 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a8"
down_revision: str | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chat_messages", sa.Column("reasoning_tokens", sa.Integer(), nullable=True))
    op.add_column("chat_messages", sa.Column("embedding_tokens", sa.Integer(), nullable=True))
    op.add_column("chat_messages", sa.Column("guardrail_tokens", sa.Integer(), nullable=True))
    op.add_column("chat_messages", sa.Column("cost_usd", sa.Float(), nullable=True))
    op.add_column("chat_messages", sa.Column("token_details", postgresql.JSON(astext_type=sa.Text()), nullable=True))

    op.create_index(
        "ix_chat_messages_session_turn",
        "chat_messages",
        ["session_id", "turn_index"],
        unique=False,
    )
    op.create_index(
        "ix_chat_messages_created_at",
        "chat_messages",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_chat_messages_created_at", table_name="chat_messages")
    op.drop_index("ix_chat_messages_session_turn", table_name="chat_messages")
    op.drop_column("chat_messages", "token_details")
    op.drop_column("chat_messages", "cost_usd")
    op.drop_column("chat_messages", "guardrail_tokens")
    op.drop_column("chat_messages", "embedding_tokens")
    op.drop_column("chat_messages", "reasoning_tokens")
