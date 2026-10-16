"""add SSO/RBAC columns on users, access_requests and auth_sessions

Revision ID: a9b0c1d2e3f4
Revises: f8a9b0c1d2e3
Create Date: 2026-10-15 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9b0c1d2e3f4"
down_revision: str | None = "f8a9b0c1d2e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLES = "('superadmin', 'admin', 'operator')"
_TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    # --- users: SSO identity, role and status --------------------------------
    op.alter_column(
        "users", "password_hash", existing_type=sa.String(256), nullable=True
    )
    op.add_column("users", sa.Column("entra_oid", sa.String(64), nullable=True))
    op.add_column("users", sa.Column("tenant_id", sa.String(64), nullable=True))
    op.add_column("users", sa.Column("role", sa.String(16), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="pending"
        ),
    )
    op.add_column("users", sa.Column("last_login_at", _TZ, nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "approved_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("users", sa.Column("approved_at", _TZ, nullable=True))
    op.add_column(
        "users",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_unique_constraint("uq_users_entra_oid", "users", ["entra_oid"])
    op.create_check_constraint(
        "ck_users_role", "users", f"role IS NULL OR role IN {_ROLES}"
    )
    op.create_check_constraint(
        "ck_users_status",
        "users",
        "status IN ('pending', 'active', 'rejected', 'suspended')",
    )

    # --- access_requests -------------------------------------------------------
    op.create_table(
        "access_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("requested_role", sa.String(16), nullable=True),
        sa.Column("justification", sa.Text(), nullable=True),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="pending"
        ),
        sa.Column(
            "decided_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("decided_at", _TZ, nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at", _TZ, nullable=False, server_default=sa.text("now()")
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="ck_access_requests_status",
        ),
        sa.CheckConstraint(
            f"requested_role IS NULL OR requested_role IN {_ROLES}",
            name="ck_access_requests_requested_role",
        ),
    )
    op.create_index("ix_access_requests_user_id", "access_requests", ["user_id"])
    # One open (pending) request per user.
    op.create_index(
        "uq_access_requests_one_pending",
        "access_requests",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )

    # --- auth_sessions ---------------------------------------------------------
    op.create_table(
        "auth_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("id_hash", sa.String(64), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at", _TZ, nullable=False, server_default=sa.text("now()")
        ),
        sa.Column("expires_at", _TZ, nullable=False),
        sa.Column("idle_expires_at", _TZ, nullable=False),
        sa.Column("last_seen_at", _TZ, nullable=False),
        sa.Column("revoked_at", _TZ, nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.UniqueConstraint("id_hash", name="uq_auth_sessions_id_hash"),
    )
    op.create_index(
        "ix_auth_sessions_user_revoked", "auth_sessions", ["user_id", "revoked_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_auth_sessions_user_revoked", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_index("uq_access_requests_one_pending", table_name="access_requests")
    op.drop_index("ix_access_requests_user_id", table_name="access_requests")
    op.drop_table("access_requests")

    op.drop_constraint("ck_users_status", "users", type_="check")
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.drop_constraint("uq_users_entra_oid", "users", type_="unique")
    for column in (
        "version",
        "approved_at",
        "approved_by",
        "last_login_at",
        "status",
        "role",
        "tenant_id",
        "entra_oid",
    ):
        op.drop_column("users", column)
    # SSO-only users have no password; give them an unusable marker so the
    # legacy NOT NULL constraint can be restored without deleting anyone.
    op.execute("UPDATE users SET password_hash = '!' WHERE password_hash IS NULL")
    op.alter_column(
        "users", "password_hash", existing_type=sa.String(256), nullable=False
    )
