"""console search / real-time indexes

Revision ID: 10d09e060b7d
Revises: bd881e8aa812
Create Date: 2026-10-20 09:00:00.000000

- ``message_feedback.updated_at``: the dashboards' data stamp (checked every
  few seconds by the jobs worker for real-time refresh) reads its max.
- Trigram (``pg_trgm``) GIN indexes for the Governance searches: the query
  text of query-audit rows (Traces ``q``) and ``latency_metrics.request_id``.
  ``pg_trgm`` is a trusted extension (PostgreSQL 13+); when the role may not
  create it the trigram indexes are skipped (searches still work, unindexed).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "10d09e060b7d"
down_revision: str | None = "bd881e8aa812"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TRGM_INDEXES = (
    (
        "ix_audit_log_query_text_trgm",
        "CREATE INDEX IF NOT EXISTS ix_audit_log_query_text_trgm ON audit_log "
        "USING gin ((CAST(detail AS jsonb) ->> 'query') gin_trgm_ops) "
        "WHERE action = 'query'",
    ),
    (
        "ix_latency_metrics_request_id_trgm",
        "CREATE INDEX IF NOT EXISTS ix_latency_metrics_request_id_trgm "
        "ON latency_metrics USING gin (request_id gin_trgm_ops)",
    ),
)


def upgrade() -> None:
    op.create_index(
        "ix_message_feedback_updated_at", "message_feedback", ["updated_at"]
    )
    bind = op.get_bind()
    try:
        with bind.begin_nested():
            bind.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    except sa.exc.DBAPIError:
        # No privilege to create the extension: keep the plain indexes only.
        return
    for _name, ddl in _TRGM_INDEXES:
        bind.execute(sa.text(ddl))


def downgrade() -> None:
    for name, _ddl in _TRGM_INDEXES:
        op.execute(sa.text(f"DROP INDEX IF EXISTS {name}"))
    op.drop_index("ix_message_feedback_updated_at", table_name="message_feedback")
