"""add usage_turns (per-turn usage ledger kept by triggers) and content search index

Revision ID: 03485c08b57b
Revises: 4dd0ee337613
Create Date: 2026-10-20 18:00:00.000000

The Usage & Cost lists (All Users / All Sessions / All Queries, CSV export)
paired every user message with its assistant message (a self-join of
``chat_messages`` by ``(session_id, turn_index)``) and outer-joined
``latency_metrics`` on each request, then grouped the whole history. A list
page that was not cached took 0.5-3 s.

``usage_turns`` holds one narrow row per such pair: exactly the rows of that
join, with the user turn's time and IP, the session's IP, the assistant turn's
request id, outcome, groundedness, token counts, cost and the request's total
latency. Row triggers keep it in step inside the writing transaction (insert,
update, delete or truncate of ``chat_messages``; latency rows; a session's
``created_ip``), so it is never behind the data. The lists aggregate and page
it through narrow indexes.

Trigram (``pg_trgm``) GIN indexes on ``chat_messages.content``,
``chat_sessions.title`` and the ledger's IP / request id serve the All
Queries text search (built ``CONCURRENTLY``; skipped when the extension
is unavailable, the search then still works unindexed).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "03485c08b57b"
down_revision: str | None = "4dd0ee337613"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "usage_turns"
# Trigram indexes of the All Queries search (table, column).
_TRGM = {
    "ix_chat_messages_content_trgm": ("chat_messages", "content"),
    "ix_chat_sessions_title_trgm": ("chat_sessions", "title"),
    "ix_usage_turns_user_ip_trgm": ("usage_turns", "user_ip"),
    "ix_usage_turns_request_id_trgm": ("usage_turns", "request_id"),
}

# The ledger rows of the pairs selected by ``{where}`` (``u`` user message,
# ``a`` assistant message): the join the usage lists ran per request.
_PAIRS_SQL = """
    SELECT a.id, u.id, u.session_id, u.turn_index, u.created_at, u.ip_address,
           s.created_ip, a.request_id, a.outcome, a.grounded, a.prompt_tokens,
           a.completion_tokens, a.reasoning_tokens, a.total_tokens,
           a.embedding_tokens, a.guardrail_tokens, a.cost_usd,
           (SELECT l.total_ms FROM latency_metrics l
             WHERE l.request_id = a.request_id
             ORDER BY l.created_at DESC LIMIT 1)
    FROM chat_messages u
    JOIN chat_messages a
      ON a.session_id = u.session_id AND a.turn_index = u.turn_index
     AND a.role = 'assistant'
    JOIN chat_sessions s ON s.id = u.session_id
    WHERE u.role = 'user' {where}
"""
_COLUMNS = (
    "asst_msg_id, user_msg_id, session_id, turn_index, created_at, user_ip, "
    "session_ip, request_id, outcome, grounded, prompt_tokens, "
    "completion_tokens, reasoning_tokens, total_tokens, embedding_tokens, "
    "guardrail_tokens, cost_usd, latency_ms"
)
_INSERT = f"INSERT INTO usage_turns ({_COLUMNS})"

_PAIRS_OF_NEW = _PAIRS_SQL.format(
    where="AND u.session_id = NEW.session_id AND u.turn_index = NEW.turn_index "
    "AND (u.id = NEW.id OR a.id = NEW.id)"
)
_FUNCTIONS = {
    "usage_turns_sync_message": f"""
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                DELETE FROM usage_turns
                 WHERE user_msg_id = OLD.id OR asst_msg_id = OLD.id;
            END IF;
            IF TG_OP <> 'DELETE' THEN
                IF NEW.role IN ('user', 'assistant') THEN
                    {_INSERT} {_PAIRS_OF_NEW}
                    ON CONFLICT DO NOTHING;
                END IF;
            END IF;
            RETURN NULL;
        END;
    """,  # noqa: S608 - constant DDL, no input
    "usage_turns_truncate": """
        BEGIN
            TRUNCATE usage_turns;
            RETURN NULL;
        END;
    """,
    "usage_turns_sync_latency": """
        DECLARE
            ids text[];
        BEGIN
            IF TG_OP = 'INSERT' THEN
                ids := ARRAY[NEW.request_id];
            ELSIF TG_OP = 'DELETE' THEN
                ids := ARRAY[OLD.request_id];
            ELSE
                ids := ARRAY[NEW.request_id, OLD.request_id];
            END IF;
            UPDATE usage_turns t
               SET latency_ms = (SELECT l.total_ms FROM latency_metrics l
                                  WHERE l.request_id = t.request_id
                                  ORDER BY l.created_at DESC LIMIT 1)
             WHERE t.request_id = ANY (ids);
            RETURN NULL;
        END;
    """,
    "usage_turns_sync_session_ip": """
        BEGIN
            UPDATE usage_turns SET session_ip = NEW.created_ip
             WHERE session_id = NEW.id
               AND session_ip IS DISTINCT FROM NEW.created_ip;
            RETURN NULL;
        END;
    """,
}

# Columns of chat_messages the ledger copies (content edits do not touch it).
_MESSAGE_COLUMNS = (
    "session_id, turn_index, role, created_at, ip_address, request_id, outcome, "
    "grounded, prompt_tokens, completion_tokens, total_tokens, reasoning_tokens, "
    "embedding_tokens, guardrail_tokens, cost_usd"
)
_TRIGGERS = (
    (
        "trg_usage_turns_message_insert",
        "AFTER INSERT ON chat_messages FOR EACH ROW",
        "usage_turns_sync_message",
    ),
    (
        "trg_usage_turns_message_update",
        f"AFTER UPDATE OF {_MESSAGE_COLUMNS} ON chat_messages FOR EACH ROW",
        "usage_turns_sync_message",
    ),
    (
        "trg_usage_turns_message_delete",
        "AFTER DELETE ON chat_messages FOR EACH ROW",
        "usage_turns_sync_message",
    ),
    (
        "trg_usage_turns_message_truncate",
        "AFTER TRUNCATE ON chat_messages FOR EACH STATEMENT",
        "usage_turns_truncate",
    ),
    (
        "trg_usage_turns_latency",
        "AFTER INSERT OR UPDATE OF request_id, total_ms, created_at OR DELETE "
        "ON latency_metrics FOR EACH ROW",
        "usage_turns_sync_latency",
    ),
    (
        "trg_usage_turns_session_ip",
        "AFTER UPDATE OF created_ip ON chat_sessions FOR EACH ROW",
        "usage_turns_sync_session_ip",
    ),
)
_TRIGGER_TABLES = {
    "trg_usage_turns_message_insert": "chat_messages",
    "trg_usage_turns_message_update": "chat_messages",
    "trg_usage_turns_message_delete": "chat_messages",
    "trg_usage_turns_message_truncate": "chat_messages",
    "trg_usage_turns_latency": "latency_metrics",
    "trg_usage_turns_session_ip": "chat_sessions",
}

# (name, columns): sort / filter / group keys of the lists.
# (name, columns): trigger lookups, filters, and one index per All Queries
# sort: ORDER BY <key> <dir> NULLS LAST, created_at DESC, asst_msg_id (a page
# at any offset is an index-ordered scan, no sort of the matching turns).
_INDEXES = (
    ("ix_usage_turns_user_msg", ["user_msg_id"]),
    ("ix_usage_turns_session", ["session_id"]),
    ("ix_usage_turns_request", ["request_id"]),
    ("ix_usage_turns_client_ip", [sa.text("coalesce(user_ip, session_ip)")]),
    *(
        (
            f"ix_usage_turns_created_{direction.lower()}",
            [sa.text(f"created_at {direction} NULLS LAST"), "asst_msg_id"],
        )
        for direction in ("DESC", "ASC")
    ),
    *(
        (
            f"ix_usage_turns_{column}_{direction.lower()}",
            [
                sa.text(f"{column} {direction} NULLS LAST"),
                sa.text("created_at DESC"),
                "asst_msg_id",
            ],
        )
        for column in ("cost_usd", "total_tokens", "latency_ms")
        for direction in ("DESC", "ASC")
    ),
)


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("asst_msg_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_msg_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("turn_index", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("user_ip", sa.String(64), nullable=True),
        sa.Column("session_ip", sa.String(64), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("outcome", sa.String(24), nullable=True),
        sa.Column("grounded", sa.Boolean(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("embedding_tokens", sa.Integer(), nullable=True),
        sa.Column("guardrail_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("asst_msg_id", "user_msg_id"),
    )
    for name, body in _FUNCTIONS.items():
        op.execute(
            f"CREATE OR REPLACE FUNCTION {name}() RETURNS trigger "
            f"LANGUAGE plpgsql AS $fn${body}$fn$"
        )
    # Backfill before the triggers fire, in this transaction (the lists read
    # the ledger from this revision on).
    op.execute(f"{_INSERT} {_PAIRS_SQL.format(where='')}")
    for name, columns in _INDEXES:
        op.create_index(name, TABLE, columns)
    for name, when, fn in _TRIGGERS:
        op.execute(f"CREATE TRIGGER {name} {when} EXECUTE FUNCTION {fn}()")

    bind = op.get_bind()
    try:
        with bind.begin_nested():
            bind.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    except sa.exc.DBAPIError:
        return
    with op.get_context().autocommit_block():
        for name, (table, column) in _TRGM.items():
            op.execute(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} "
                f"ON {table} USING gin ({column} gin_trgm_ops)"
            )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name in _TRGM:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
    for name, table in _TRIGGER_TABLES.items():
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
    for name in _FUNCTIONS:
        op.execute(f"DROP FUNCTION IF EXISTS {name}()")
    op.drop_table(TABLE)
