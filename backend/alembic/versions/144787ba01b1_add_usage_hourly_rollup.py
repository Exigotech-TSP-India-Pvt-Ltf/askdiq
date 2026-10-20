"""add usage_hourly_rollup and usage_rollup_state

Revision ID: 144787ba01b1
Revises: 4dd0ee337613
Create Date: 2026-10-20 17:00:00.000000

The Usage & Cost summary (KPIs, cost by stage, month-end forecast, cache-hit
ratio, latency) re-aggregated every exchange of the range on each new range.
``usage_hourly_rollup`` holds those sums per UTC hour and client IP, filled
incrementally by the snapshot refresher, so any range is a small indexed
aggregation. Reads add a live aggregation of the rows newer than
``usage_rollup_state.synced_until``, so results are unchanged.

Statement-level triggers move ``synced_until`` back when rows older than the
refresher's 30-minute overlap are inserted, updated or deleted (a deleted
chat, an import), so those hours read live until they are rolled again.
The backfill runs in the refresher, not here.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "144787ba01b1"
down_revision: str | None = "4dd0ee337613"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COUNTS = (
    "turns",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "reasoning_tokens",
    "embedding_tokens",
    "guardrail_tokens",
    "estimated_turns",
    "answered",
    "ungrounded",
    "blocked",
    "conversational",
    "llm_failed",
    "clarify",
    "ingest_rows",
)
_SUMS = ("cost_usd", "cached_tokens", "ingest_tokens", "ingest_cost")

_REOPEN_FN = """
CREATE FUNCTION usage_rollup_reopen() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    oldest timestamp;
BEGIN
    SELECT min(created_at) INTO oldest FROM changed_rows;
    IF oldest IS NOT NULL
       AND oldest < (clock_timestamp() AT TIME ZONE 'UTC') - interval '30 minutes' THEN
        UPDATE usage_rollup_state
           SET synced_until = LEAST(
                   synced_until, date_trunc('hour', oldest) - interval '1 hour'),
               version = version + 1;
    END IF;
    RETURN NULL;
END
$$
"""

# (trigger name, table, event, transition table)
_TRIGGERS = (
    ("usage_rollup_chat_insert", "chat_messages", "INSERT", "NEW"),
    ("usage_rollup_chat_update", "chat_messages", "UPDATE", "OLD"),
    ("usage_rollup_chat_delete", "chat_messages", "DELETE", "OLD"),
    ("usage_rollup_latency_insert", "latency_metrics", "INSERT", "NEW"),
    ("usage_rollup_latency_update", "latency_metrics", "UPDATE", "OLD"),
    ("usage_rollup_latency_delete", "latency_metrics", "DELETE", "OLD"),
)


def upgrade() -> None:
    op.create_table(
        "usage_hourly_rollup",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("hour", sa.DateTime(), nullable=False),
        sa.Column("asst_day", sa.DateTime(), nullable=True),
        sa.Column("ip", sa.String(64), nullable=True),
        *(sa.Column(name, sa.BigInteger(), nullable=False) for name in _COUNTS),
        *(sa.Column(name, sa.Float(), nullable=False) for name in _SUMS),
        sa.Column("latency_ms", postgresql.ARRAY(sa.Float()), nullable=True),
    )
    op.create_index("ix_usage_hourly_rollup_hour", "usage_hourly_rollup", ["hour"])
    op.create_table(
        "usage_rollup_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("synced_until", sa.DateTime(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
    )
    op.execute(_REOPEN_FN)
    for name, table, event, transition in _TRIGGERS:
        op.execute(
            f"CREATE TRIGGER {name} AFTER {event} ON {table} "
            f"REFERENCING {transition} TABLE AS changed_rows "
            "FOR EACH STATEMENT EXECUTE FUNCTION usage_rollup_reopen()"
        )


def downgrade() -> None:
    for name, table, _event, _transition in _TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
    op.execute("DROP FUNCTION IF EXISTS usage_rollup_reopen()")
    op.drop_table("usage_rollup_state")
    op.drop_index("ix_usage_hourly_rollup_hour", table_name="usage_hourly_rollup")
    op.drop_table("usage_hourly_rollup")
