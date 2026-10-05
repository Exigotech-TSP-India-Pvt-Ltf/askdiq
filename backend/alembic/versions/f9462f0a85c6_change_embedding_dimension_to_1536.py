"""Change embedding dimension from 768 to 1536.

Revision ID: f9462f0a85c6
Revises: 99cb28f36610
"""

from typing import Sequence, Union

from alembic import op


revision: str = "f9462f0a85c6"
down_revision: Union[str, Sequence[str], None] = "99cb28f36610"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE chunks
        ALTER COLUMN embedding
        TYPE vector(1536)
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE chunks
        ALTER COLUMN embedding
        TYPE vector(768)
        """
    )