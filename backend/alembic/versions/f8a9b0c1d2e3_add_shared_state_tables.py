"""add Postgres shared-state tables (rate_limit_buckets, oidc_states)

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-10-15 10:05:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f8a9b0c1d2e3"
down_revision: str | None = "e7f8a9b0c1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # UNLOGGED: no WAL cost; a crash only resets the counters.
    op.execute(
        """
        CREATE UNLOGGED TABLE rate_limit_buckets (
            key text NOT NULL,
            window_start timestamptz NOT NULL,
            count integer NOT NULL DEFAULT 0,
            PRIMARY KEY (key, window_start)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_rate_limit_buckets_window_start "
        "ON rate_limit_buckets (window_start)"
    )
    op.execute(
        """
        CREATE TABLE oidc_states (
            state_hash text PRIMARY KEY,
            payload jsonb NOT NULL,
            expires_at timestamptz NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX ix_oidc_states_expires_at ON oidc_states (expires_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS oidc_states")
    op.execute("DROP TABLE IF EXISTS rate_limit_buckets")
