import asyncio
from collections.abc import AsyncGenerator

from fastapi import Depends, status
from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError, IllegalStateChangeError
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, SessionTransaction

from app.core.config import get_settings
from app.core.errors import ApiHTTPException, ErrorCode
from app.core.logging_config import get_logger

settings = get_settings()
logger = get_logger(__name__)

# Close retries while another task finishes an operation on the session.
_CLOSE_RETRY_ATTEMPTS = 20
_CLOSE_RETRY_FIRST_SECONDS = 0.01
_CLOSE_RETRY_MAX_SECONDS = 0.5


def _connect_args() -> dict:
    """asyncpg server settings: end sessions idle in a transaction (safety net)."""
    timeout = settings.db_idle_in_transaction_timeout_ms
    if timeout <= 0 or "+asyncpg" not in settings.database_url:
        return {}
    return {"server_settings": {"idle_in_transaction_session_timeout": str(timeout)}}


engine = create_async_engine(
    settings.database_url,
    connect_args=_connect_args(),
    echo=settings.db_echo,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    # Validate on checkout (stale sockets behind nginx / cloud LBs) and recycle.
    pool_pre_ping=settings.db_pool_pre_ping,
    pool_recycle=settings.db_pool_recycle_seconds,
    pool_timeout=settings.db_pool_timeout_seconds,
)

# Postgres SQLSTATE for "canceling statement due to statement timeout".
QUERY_CANCELED_SQLSTATE = "57014"

# Background work (jobs worker, schedulers, samplers, ingestion scripts):
# no per-statement cap beyond the server default.
AsyncSessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

# ``Session.info`` key: per-statement timeout (ms) for this session, or
# ``UNBOUNDED`` for no API cap. Read at every transaction begin.
STATEMENT_TIMEOUT_INFO_KEY = "askdiq_statement_timeout_ms"
UNBOUNDED = 0


class RequestSession(Session):
    """Sync session class of request-scoped sessions (``RequestSessionLocal``).

    Every transaction it begins gets ``SET LOCAL statement_timeout =
    DB_STATEMENT_TIMEOUT_MS`` (see ``_apply_request_statement_timeout``), so a
    request can never hang on a query stuck behind a lock: Postgres cancels
    it and the client gets 503 ``db_timeout``.
    """


@event.listens_for(RequestSession, "after_begin")
def _apply_request_statement_timeout(
    session: Session, _transaction: SessionTransaction, connection: Connection
) -> None:
    timeout_ms = int(
        session.info.get(STATEMENT_TIMEOUT_INFO_KEY, settings.db_statement_timeout_ms)
    )
    if timeout_ms <= UNBOUNDED:
        return
    # Integer from typed settings; SET cannot take bind parameters.
    connection.exec_driver_sql(
        f"SET LOCAL statement_timeout = {timeout_ms}"  # nosec B608
    )


# Request-scoped sessions (``get_db``, ``get_session_factory``,
# ``get_scoped_runner``): bounded by ``DB_STATEMENT_TIMEOUT_MS``.
RequestSessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
    sync_session_class=RequestSession,
)


class Base(DeclarativeBase):
    """Base class for all ORM models."""


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a DB session per request.

    The close runs through ``close_session`` so a request cancelled on
    shutdown/reload (or client disconnect) still returns its connection.
    """
    session = RequestSessionLocal()
    try:
        yield session
    finally:
        await close_session(session)


async def _close_when_idle(session: AsyncSession) -> None:
    """Close ``session``, waiting out an operation still running on it.

    ``IllegalStateChangeError`` means another task is inside a session method
    (typically a connection checkout, ``_connection_for_bind``, that a
    cancelled request left running). Closing under it would corrupt the
    session, so retry briefly until it finishes; the connection is returned
    to the pool either way.
    """
    delay = _CLOSE_RETRY_FIRST_SECONDS
    for _attempt in range(_CLOSE_RETRY_ATTEMPTS):
        try:
            await session.close()
            return
        except IllegalStateChangeError:
            await asyncio.sleep(delay)
            delay = min(delay * 2, _CLOSE_RETRY_MAX_SECONDS)
    logger.warning("db_session_close_abandoned", reason="operation_in_progress")


async def close_session(session: AsyncSession) -> None:
    """Close a request session even when the request is being cancelled.

    The close runs in its own task and is shielded: cancellation of the
    caller cannot interrupt it half-way (which would leak the pooled
    connection), and a close that races a still-running operation is retried
    instead of raising ``IllegalStateChangeError`` into the request.
    """
    closer = asyncio.ensure_future(_close_when_idle(session))
    try:
        await asyncio.shield(closer)
    except asyncio.CancelledError:
        # The close keeps running in the background; let cancellation proceed.
        closer.add_done_callback(_log_close_failure)
        raise
    except Exception as exc:
        logger.warning("db_session_close_failed", error_type=type(exc).__name__)


def _log_close_failure(task: asyncio.Future[None]) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("db_session_close_failed", error_type=type(exc).__name__)


async def release_connection(session: AsyncSession) -> None:
    """End ``session``'s read transaction so its pooled connection goes back now.

    Call it before fanning out to parallel sessions (``gather_scoped``): a
    request that keeps one connection while waiting for more holds a pool slot
    idle, and enough such requests together deadlock the pool until
    ``pool_timeout``. The session reconnects on its next use.

    A session bound to a caller-owned connection (it joined an outer
    transaction, e.g. the test fixture's) holds no pool slot of its own:
    there is nothing to release, and committing would end a transaction it
    does not own while other users of that connection may be mid-statement.
    """
    if isinstance(getattr(session, "bind", None), AsyncConnection):
        return
    if session.in_transaction():
        await session.commit()


def pool_stats() -> dict[str, int]:
    """Live pool counters for this process (``/health``, load tests)."""
    pool = engine.pool
    return {
        "size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "checked_out": int(getattr(pool, "checkedout", lambda: 0)()),
        "idle": int(getattr(pool, "checkedin", lambda: 0)()),
    }


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Dependency: the factory ``gather_scoped`` opens one session per task from.

    A dependency (not a module global) so tests can route parallel
    aggregation through their transactional session. Sessions are
    request-scoped, so bounded by ``DB_STATEMENT_TIMEOUT_MS``.
    """
    return RequestSessionLocal


async def disable_statement_timeout(session: AsyncSession) -> None:
    """Opt ``session`` out of the API ``DB_STATEMENT_TIMEOUT_MS`` cap.

    For request paths that legitimately run long statements (streamed admin
    exports, document ingestion). Covers the current transaction (``SET
    LOCAL ... TO DEFAULT``, i.e. the server/role default) and every later one
    on this session. Background work should use ``AsyncSessionLocal``, which
    is never capped, rather than calling this.
    """
    session.info[STATEMENT_TIMEOUT_INFO_KEY] = UNBOUNDED
    if session.in_transaction():
        await session.execute(text("SET LOCAL statement_timeout TO DEFAULT"))


async def unbounded_session(
    session: AsyncSession = Depends(get_db),
) -> AsyncGenerator[AsyncSession, None]:
    """Dependency: the request's ``get_db`` session without the API timeout.

    Use on long-running endpoints only (exports, ingestion); builds on
    ``get_db`` so a test override of ``get_db`` covers these routes too.
    """
    await disable_statement_timeout(session)
    yield session


def is_statement_timeout(exc: BaseException) -> bool:
    """True when ``exc`` is Postgres cancelling a statement (statement_timeout)."""
    if not isinstance(exc, DBAPIError):
        return False
    return getattr(exc.orig, "sqlstate", None) == QUERY_CANCELED_SQLSTATE


async def read_only_session(
    session: AsyncSession = Depends(get_db),
) -> AsyncGenerator[AsyncSession, None]:
    """Dependency for heavy read paths (dashboards): bounded statement time.

    Builds on the request's ``get_db`` session (one connection per request,
    and overriding ``get_db`` covers these routes too). ``SET LOCAL
    statement_timeout`` lasts for the request's transaction; a query that
    exceeds ``DASHBOARD_STATEMENT_TIMEOUT_MS`` is cancelled by Postgres and
    surfaces as a clean 503 instead of a hung worker.
    """
    timeout_ms = int(settings.dashboard_statement_timeout_ms)
    # Later transactions on this session keep the stricter cap too.
    session.info[STATEMENT_TIMEOUT_INFO_KEY] = timeout_ms
    # Integer from typed settings; SET cannot take bind parameters.
    await session.execute(
        text(f"SET LOCAL statement_timeout = {timeout_ms}")  # nosec B608
    )
    try:
        yield session
    except DBAPIError as exc:
        if is_statement_timeout(exc):
            raise ApiHTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="The query took too long. Please retry or narrow the range.",
                code=ErrorCode.DB_TIMEOUT,
                headers={"Retry-After": "5"},
            ) from None
        raise


async def init_db() -> None:
    """Ensure the pgvector extension is enabled."""
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
