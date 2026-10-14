"""enforce audit_log immutability and add indexes

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-10-13 11:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Indexes on audit_log for efficient filtering and trace lookups
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_log_action_created_at ON audit_log (action, created_at DESC);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_log_request_id ON audit_log (request_id);"
    )

    # 2. Immutability trigger function and trigger to enforce append-only at the DB level (OWASP A09)
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_audit_log_modification()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only and cannot be updated or deleted';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_log_immutable
        BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW
        EXECUTE FUNCTION prevent_audit_log_modification();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_log_immutable ON audit_log;")
    op.execute("DROP FUNCTION IF EXISTS prevent_audit_log_modification();")
    op.execute("DROP INDEX IF EXISTS ix_audit_log_action_created_at;")
    op.execute("DROP INDEX IF EXISTS ix_audit_log_request_id;")
