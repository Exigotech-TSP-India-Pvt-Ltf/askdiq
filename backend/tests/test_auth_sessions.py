"""Session lifecycle (B2.3.1) and the auth migration (B2.1.1); Postgres, runs in CI."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import UserStatus
from app.auth.sessions import SessionService, hash_session_id
from app.core.config import get_settings
from app.models.db_models import AccessRequest, AuthSession
from tests.auth_helpers import make_user
from tests.pg_helpers import new_engine, require_postgres

BACKEND_DIR = Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def service(db_session: AsyncSession, clock: Clock) -> SessionService:
    return SessionService(db_session, clock=clock)


async def _row(db: AsyncSession, session_id: uuid.UUID) -> AuthSession:
    db.expire_all()
    return (
        await db.execute(select(AuthSession).where(AuthSession.id == session_id))
    ).scalar_one()


async def test_raw_session_id_is_never_stored(
    db_session: AsyncSession, service: SessionService
) -> None:
    user = await make_user(db_session, role="operator", status="active")
    issued = await service.create_session(
        user.id, ip_address="203.0.113.9", user_agent="pytest"
    )
    assert len(issued.token) >= 43

    row = await _row(db_session, issued.session_id)
    assert row.id_hash != issued.token
    assert row.id_hash == hash_session_id(issued.token)
    assert len(row.id_hash) == 64
    stored = (
        await db_session.execute(
            text("SELECT auth_sessions::text FROM auth_sessions WHERE id = :id"),
            {"id": issued.session_id},
        )
    ).scalar_one()
    assert issued.token not in stored


async def test_session_resolves_to_the_fresh_user_row(
    db_session: AsyncSession, service: SessionService
) -> None:
    user = await make_user(db_session, role="operator", status="active")
    issued = await service.create_session(user.id)
    resolved = await service.resolve(issued.token)
    assert resolved is not None
    assert resolved.id == user.id
    assert resolved.email == user.email
    assert resolved.status is UserStatus.ACTIVE
    assert resolved.session_id == issued.session_id
    assert await service.resolve("not-a-session") is None
    assert await service.resolve("x" * 500) is None
    assert await service.resolve("") is None


async def test_absolute_expiry(
    db_session: AsyncSession, service: SessionService, clock: Clock
) -> None:
    user = await make_user(db_session, status="pending")
    issued = await service.create_session(user.id)
    ttl = get_settings().session_ttl_minutes
    idle_step = get_settings().session_idle_minutes - 1
    # Stay active (never idle) until the absolute TTL is reached.
    elapsed = 0
    while elapsed + idle_step < ttl:
        clock.advance(minutes=idle_step)
        elapsed += idle_step
        assert await service.resolve(issued.token) is not None
    clock.advance(minutes=ttl - elapsed)
    assert await service.resolve(issued.token) is None


async def test_idle_expiry(
    db_session: AsyncSession, service: SessionService, clock: Clock
) -> None:
    user = await make_user(db_session, status="pending")
    issued = await service.create_session(user.id)
    clock.advance(minutes=get_settings().session_idle_minutes + 1)
    assert await service.resolve(issued.token) is None


async def test_last_seen_is_written_at_most_once_a_minute(
    db_session: AsyncSession, service: SessionService, clock: Clock
) -> None:
    user = await make_user(db_session, status="pending")
    issued = await service.create_session(user.id)
    created = clock.now

    clock.advance(seconds=30)
    await service.resolve(issued.token)
    assert (await _row(db_session, issued.session_id)).last_seen_at == created

    clock.advance(seconds=40)
    await service.resolve(issued.token)
    row = await _row(db_session, issued.session_id)
    assert row.last_seen_at == clock.now
    assert row.idle_expires_at == clock.now + timedelta(
        minutes=get_settings().session_idle_minutes
    )


async def test_revoke_and_revoke_all(
    db_session: AsyncSession, service: SessionService
) -> None:
    user = await make_user(db_session, role="admin", status="active")
    first = await service.create_session(user.id)
    second = await service.create_session(user.id)
    third = await service.create_session(user.id)

    assert await service.revoke(first.token) is True
    assert await service.revoke(first.token) is False
    assert await service.resolve(first.token) is None
    assert await service.resolve(second.token) is not None

    assert await service.revoke_all(user.id) == 2
    assert await service.resolve(second.token) is None
    assert await service.resolve(third.token) is None
    assert (await _row(db_session, third.session_id)).revoked_at is not None


async def test_login_rotates_the_session(
    db_session: AsyncSession, service: SessionService
) -> None:
    user = await make_user(db_session, status="pending")
    old = await service.create_session(user.id)
    new = await service.create_session(user.id, replaces=old.token)
    assert new.token != old.token
    assert await service.resolve(old.token) is None
    assert await service.resolve(new.token) is not None


async def test_one_pending_access_request_per_user(db_session: AsyncSession) -> None:
    user = await make_user(db_session, status="pending")
    db_session.add(AccessRequest(user_id=user.id, status="rejected"))
    db_session.add(AccessRequest(user_id=user.id, status="pending"))
    await db_session.flush()

    db_session.add(AccessRequest(user_id=user.id, status="pending"))
    with pytest.raises(IntegrityError, match="uq_access_requests_one_pending"):
        await db_session.flush()
    await db_session.rollback()


def _alembic(*args: str) -> str:
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout + result.stderr


async def test_auth_migration_upgrade_downgrade_upgrade() -> None:
    engine = new_engine()
    await require_postgres(engine)
    try:
        heads = await asyncio.to_thread(_alembic, "heads")
        assert heads.count("(head)") == 1
        # Later migrations stack on top (planchanges4 8.3): the auth revision
        # must be on the single linear chain to head, not necessarily head.
        chain = await asyncio.to_thread(_alembic, "history", "-r", "a9b0c1d2e3f4:head")
        assert "a9b0c1d2e3f4" in chain

        await asyncio.to_thread(_alembic, "downgrade", "f8a9b0c1d2e3")
        async with engine.connect() as conn:
            tables = await conn.scalar(
                text(
                    "SELECT count(*) FROM information_schema.tables WHERE "
                    "table_name IN ('access_requests', 'auth_sessions')"
                )
            )
            role_col = await conn.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_name = 'users' AND column_name = 'entra_oid'"
                )
            )
        await engine.dispose()
        assert tables == 0
        assert role_col == 0

        await asyncio.to_thread(_alembic, "upgrade", "head")
        async with engine.connect() as conn:
            tables = await conn.scalar(
                text(
                    "SELECT count(*) FROM information_schema.tables WHERE "
                    "table_name IN ('access_requests', 'auth_sessions')"
                )
            )
            nullable = await conn.scalar(
                text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_name = 'users' AND column_name = 'password_hash'"
                )
            )
        assert tables == 2
        assert nullable == "YES"
    finally:
        await engine.dispose()
