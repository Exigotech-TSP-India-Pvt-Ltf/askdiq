"""feedback triage columns, eval_promoted_cases, import promoted JSON

Revision ID: 5712ebd9d3c9
Revises: a9b0c1d2e3f4
Create Date: 2026-10-15 15:00:00.000000

"""

import json
import uuid
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5712ebd9d3c9"
down_revision: str | None = "a9b0c1d2e3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUSES = "('new', 'reviewed', 'promoted', 'dismissed')"
PROMOTED_JSON = Path(__file__).resolve().parents[2] / "data" / "promoted_test_cases.json"

_INSERT_CASE = sa.text(
    """
    INSERT INTO eval_promoted_cases
        (id, query, case_type, expect_grounded, expected_outcome,
         source_message_id, promoted_by, promoted_at)
    VALUES (:id, :query, :case_type, :expect_grounded, :expected_outcome,
            CAST(:source_message_id AS uuid), :promoted_by, :promoted_at)
    ON CONFLICT DO NOTHING
    """
)

_LINK_FEEDBACK = sa.text(
    """
    UPDATE message_feedback AS f
    SET triage_status = 'promoted', promoted_case_id = c.id
    FROM eval_promoted_cases AS c
    WHERE f.message_id = c.source_message_id
      AND f.promoted_case_id IS NULL
      AND f.triage_status IN ('new', 'reviewed')
    """
)


def _parse_uuid(value: object) -> str | None:
    try:
        return str(uuid.UUID(str(value))) if value else None
    except ValueError:
        return None


def _parse_dt(value: object) -> datetime:
    try:
        return datetime.fromisoformat(str(value).removesuffix("Z"))
    except ValueError:
        return datetime.utcnow()


def import_promoted_cases(conn: sa.engine.Connection, path: Path) -> int:
    """Copy the legacy JSON cases into the table; idempotent, missing file = 0."""
    if not path.is_file():
        return 0
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    rows = [
        {
            "id": str(c["id"])[:64],
            "query": str(c["query"]),
            "case_type": str(c.get("case_type") or "robustness")[:32],
            "expect_grounded": bool(c.get("expect_grounded", True)),
            "expected_outcome": (c.get("expected_outcome") or None),
            "source_message_id": _parse_uuid(c.get("source_message_id")),
            "promoted_by": str(c.get("promoted_by") or "legacy-import")[:256],
            "promoted_at": _parse_dt(c.get("promoted_at")),
        }
        for c in (raw if isinstance(raw, list) else [])
        if isinstance(c, dict) and c.get("id") and c.get("query")
    ]
    if rows:
        conn.execute(_INSERT_CASE, rows)
    conn.execute(_LINK_FEEDBACK)
    return len(rows)


def upgrade() -> None:
    op.create_table(
        "eval_promoted_cases",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column(
            "case_type", sa.String(32), nullable=False, server_default="robustness"
        ),
        sa.Column(
            "expect_grounded",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("expected_outcome", sa.String(24), nullable=True),
        sa.Column("source_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("feedback_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reference_answer", sa.Text(), nullable=True),
        sa.Column(
            "reason_tags",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("promoted_by", sa.String(256), nullable=False),
        sa.Column(
            "promoted_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_run_passed", sa.Boolean(), nullable=True),
        sa.Column("last_run_detail", postgresql.JSONB(), nullable=True),
        sa.UniqueConstraint(
            "source_message_id", name="uq_eval_promoted_cases_source_message_id"
        ),
    )
    op.create_index(
        "ix_eval_promoted_cases_promoted_at", "eval_promoted_cases", ["promoted_at"]
    )

    op.add_column(
        "message_feedback",
        sa.Column(
            "reason_tags",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "message_feedback", sa.Column("corrected_answer", sa.Text(), nullable=True)
    )
    op.add_column(
        "message_feedback",
        sa.Column(
            "triage_status", sa.String(16), nullable=False, server_default="new"
        ),
    )
    op.add_column(
        "message_feedback", sa.Column("reviewed_by", sa.String(256), nullable=True)
    )
    op.add_column(
        "message_feedback", sa.Column("reviewed_at", sa.DateTime(), nullable=True)
    )
    op.add_column(
        "message_feedback", sa.Column("review_note", sa.Text(), nullable=True)
    )
    op.add_column(
        "message_feedback",
        sa.Column(
            "promoted_case_id",
            sa.String(64),
            sa.ForeignKey(
                "eval_promoted_cases.id",
                name="fk_message_feedback_promoted_case",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "message_feedback",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_check_constraint(
        "ck_message_feedback_triage_status",
        "message_feedback",
        f"triage_status IN {_STATUSES}",
    )
    op.create_index(
        "ix_message_feedback_triage_created",
        "message_feedback",
        ["triage_status", "created_at"],
    )
    op.create_index(
        "ix_message_feedback_created_id", "message_feedback", ["created_at", "id"]
    )

    # Data step: move the legacy promoted-cases file into the table (A08).
    import_promoted_cases(op.get_bind(), PROMOTED_JSON)


def downgrade() -> None:
    op.drop_index("ix_message_feedback_created_id", table_name="message_feedback")
    op.drop_index("ix_message_feedback_triage_created", table_name="message_feedback")
    op.drop_constraint(
        "ck_message_feedback_triage_status", "message_feedback", type_="check"
    )
    for column in (
        "version",
        "promoted_case_id",
        "review_note",
        "reviewed_at",
        "reviewed_by",
        "triage_status",
        "corrected_answer",
        "reason_tags",
    ):
        op.drop_column("message_feedback", column)
    op.drop_index(
        "ix_eval_promoted_cases_promoted_at", table_name="eval_promoted_cases"
    )
    op.drop_table("eval_promoted_cases")
