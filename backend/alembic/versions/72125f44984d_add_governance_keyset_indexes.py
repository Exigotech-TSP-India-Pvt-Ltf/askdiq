"""add governance keyset and join indexes

Revision ID: 72125f44984d
Revises: 5712ebd9d3c9
Create Date: 2026-10-15 16:00:00.000000

Composite indexes for the Governance endpoints (planchanges4 B4.2, newplan
§13.2):

- ``audit_log (created_at, id)``: keyset pagination of ``/governance/logs``
  (newest first = backward index scan) and the export stream.
- ``audit_log (action, created_at, id)``: action / service filters, the
  ``server_error`` hourly rollup and incident lookups.
- ``audit_log (request_id, action)``: the per-trace ``query`` audit row
  (LATERAL join from ``/governance/traces``) and audience-scoped joins.
- ``latency_metrics (created_at, id)``: keyset pagination of traces.
- ``chat_messages (request_id, role)``: the per-trace assistant turn.

``audit_log`` and ``chat_messages`` grow without bound, so the indexes are
built ``CONCURRENTLY`` (no write lock on a live table). Postgres refuses
``CREATE INDEX CONCURRENTLY`` inside a transaction block, and ``env.py`` runs
migrations in one transaction, so each statement runs in Alembic's
``autocommit_block()`` (which commits the surrounding transaction first).
``IF NOT EXISTS`` makes a retry after an interrupted concurrent build safe.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "72125f44984d"
down_revision: str | None = "5712ebd9d3c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEXES: tuple[tuple[str, str, list[str]], ...] = (
    ("ix_audit_log_created_id", "audit_log", ["created_at", "id"]),
    ("ix_audit_log_action_created_id", "audit_log", ["action", "created_at", "id"]),
    ("ix_audit_log_request_action", "audit_log", ["request_id", "action"]),
    ("ix_latency_metrics_created_id", "latency_metrics", ["created_at", "id"]),
    ("ix_chat_messages_request_role", "chat_messages", ["request_id", "role"]),
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for name, table, columns in INDEXES:
            op.create_index(
                name,
                table,
                columns,
                unique=False,
                postgresql_concurrently=True,
                if_not_exists=True,
            )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name, table, _columns in reversed(INDEXES):
            op.drop_index(
                name,
                table_name=table,
                postgresql_concurrently=True,
                if_exists=True,
            )
