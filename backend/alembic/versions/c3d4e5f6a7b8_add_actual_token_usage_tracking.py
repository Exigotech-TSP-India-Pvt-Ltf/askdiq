"""add actual token usage tracking to chat messages

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-10-07 23:20:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_messages", sa.Column("prompt_tokens", sa.Integer(), nullable=True)
    )
    op.add_column(
        "chat_messages", sa.Column("completion_tokens", sa.Integer(), nullable=True)
    )
    op.add_column(
        "chat_messages", sa.Column("total_tokens", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("chat_messages", "total_tokens")
    op.drop_column("chat_messages", "completion_tokens")
    op.drop_column("chat_messages", "prompt_tokens")
