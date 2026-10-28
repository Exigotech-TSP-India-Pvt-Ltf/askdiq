import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.core.config import get_settings
from app.core.database import Base
from app.core.migration_guard import apply_migration_timeouts, run_with_lock_retries
from app.models import db_models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


settings = get_settings()

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""

    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    apply_migration_timeouts(
        connection,
        lock_timeout_ms=settings.migration_lock_timeout_ms,
        statement_timeout_ms=settings.migration_statement_timeout_ms,
    )
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """One upgrade attempt on a fresh asyncpg connection."""

    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = settings.database_url

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    try:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (asyncpg).

    Each attempt uses a fresh connection with ``lock_timeout`` set, so a
    migration blocked on a table lock fails fast (SQLSTATE 55P03) instead of
    queueing every API query behind its pending ACCESS EXCLUSIVE lock; the
    transactional upgrade is rolled back and retried with backoff
    (``MIGRATION_LOCK_*`` settings).
    """

    run_with_lock_retries(
        lambda: asyncio.run(run_async_migrations()),
        retries=settings.migration_lock_retries,
        base_backoff_seconds=settings.migration_lock_retry_backoff_seconds,
    )


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
