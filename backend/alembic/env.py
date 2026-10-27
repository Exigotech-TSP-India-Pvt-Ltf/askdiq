from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

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


def get_sync_database_url() -> str:
    """Convert the application's async DB URL to a sync URL for Alembic."""
    return settings.database_url.replace(
        "postgresql+asyncpg://",
        "postgresql+psycopg2://",
        1,
    )


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""

    url = get_sync_database_url()

    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    Each attempt uses a fresh connection with ``lock_timeout`` set, so a
    migration blocked on a table lock fails fast (SQLSTATE 55P03) instead of
    queueing every API query behind its pending ACCESS EXCLUSIVE lock; the
    transactional upgrade is rolled back and retried with backoff
    (``MIGRATION_LOCK_*`` settings).
    """

    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_sync_database_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    def upgrade_once() -> None:
        with connectable.connect() as connection:
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

    try:
        run_with_lock_retries(
            upgrade_once,
            retries=settings.migration_lock_retries,
            base_backoff_seconds=settings.migration_lock_retry_backoff_seconds,
        )
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
