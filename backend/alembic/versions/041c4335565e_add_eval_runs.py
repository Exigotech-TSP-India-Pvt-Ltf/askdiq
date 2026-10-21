"""add eval_runs (full benchmark runs started from the Evaluation page)

Revision ID: 041c4335565e
Revises: 144fe4240293
Create Date: 2026-10-22 10:00:00.000000

One row per ``eval.full_run`` job: who started it, its options (judge, case
types), live progress, the report summary and the per-case results. The
Evaluation page reads the newest succeeded row instead of a report file, so
the history survives container recreation. A partial unique index on a
constant keeps at most one run queued or running at a time.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "041c4335565e"
down_revision: str | None = "144fe4240293"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "eval_runs"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("started_by", sa.String(256), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column(
            "options",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cases_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cases_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cases_passed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("pass_rate", sa.Float(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("summary", postgresql.JSONB(), nullable=True),
        sa.Column("case_results", postgresql.JSONB(), nullable=True),
        sa.Column("report_file", sa.String(128), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_eval_runs"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_eval_runs_status",
        ),
    )
    op.create_index("ix_eval_runs_created_at", TABLE, ["created_at"])
    op.create_index(
        "ix_eval_runs_succeeded_finished",
        TABLE,
        ["finished_at"],
        postgresql_where=sa.text("status = 'succeeded'"),
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_eval_runs_one_active ON eval_runs ((true)) "
        "WHERE status IN ('queued', 'running')"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_eval_runs_one_active")
    op.drop_index("ix_eval_runs_succeeded_finished", table_name=TABLE)
    op.drop_index("ix_eval_runs_created_at", table_name=TABLE)
    op.drop_table(TABLE)
