"""add evaluation_records table

Revision ID: d6e7f8a9b0c1
Revises: d5e6f7a8b9c0
Create Date: 2026-10-14 15:55:00.000000

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d6e7f8a9b0c1"
down_revision: str | None = "d5e6f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evaluation_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_id", sa.String(length=64), nullable=False, index=True),
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_messages.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "audience",
            sa.String(length=32),
            nullable=False,
            server_default="users",
            index=True,
        ),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False, default=True),
        sa.Column("grounded", sa.Boolean(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=True),
        sa.Column("faithfulness", sa.Float(), nullable=True),
        sa.Column("relevance", sa.Float(), nullable=True),
        sa.Column("completeness", sa.Float(), nullable=True),
        sa.Column("citation_precision", sa.Float(), nullable=True),
        sa.Column("citation_coverage", sa.Float(), nullable=True),
        sa.Column("retrieval_recall", sa.Float(), nullable=True),
        sa.Column("safety_passed", sa.Boolean(), nullable=False, default=True),
        sa.Column(
            "guardrail_flags", postgresql.JSON(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "retrieved_chunks", postgresql.JSON(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("judge_reason", sa.Text(), nullable=True),
        sa.Column(
            "unsupported_claims", postgresql.JSON(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
            index=True,
        ),
    )
    op.create_index(
        "ix_eval_records_audience_created",
        "evaluation_records",
        ["audience", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_eval_records_passed",
        "evaluation_records",
        ["passed"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_eval_records_passed", table_name="evaluation_records")
    op.drop_index("ix_eval_records_audience_created", table_name="evaluation_records")
    op.drop_table("evaluation_records")
