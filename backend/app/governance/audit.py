"""Writes an audit trail entry for every query/ingest/guardrail event.

Every query is logged with its request id, actor, retrieved chunk ids,
guardrail decisions, and response — giving a reproducible record of what
the system did and why, which is the backbone of the governance
requirement (not just guardrails, but auditability of decisions).

Two write paths:
  - ``record_audit_event`` adds a row to the caller's session (same
    transaction as the business change it describes).
  - ``emit_audit_event`` is fire-and-forget for request-path signals
    (429s, 5xx, LLM failures, session lifecycle). Rows are PII-masked,
    queued, and flushed in batches by one supervised background task
    (``app.core.tasks.spawn``) per event loop,
    so the request path never waits on (or fails because of) the insert.

``audit_log`` is append-only (trigger ``trg_audit_log_immutable``); both
paths only ever INSERT.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.core.events import TOPIC_GOVERNANCE_INCIDENT, publish_event
from app.core.logging_config import get_logger
from app.core.tasks import spawn
from app.governance.log_summary import INCIDENT_LEVELS, level_for
from app.governance.pii import redact_pii
from app.models.db_models import AuditLog

logger = get_logger(__name__)

# Events not tied to a specific request still need a non-null request_id.
SYSTEM_REQUEST_ID = "system"
_MAX_DETAIL_STR = 500
_MAX_QUEUE = 5000

_queue: list[dict[str, Any]] = []


class _Flusher:
    """Holds the per-loop flush task (avoids a module-level ``global``)."""

    task: asyncio.Task | None = None


async def record_audit_event(
    session: AsyncSession,
    request_id: str,
    actor: str,
    action: str,
    detail: dict,
    ip_address: str | None = None,
    commit: bool = True,
) -> None:
    row = AuditLog(
        request_id=request_id,
        actor=actor,
        action=action,
        detail=detail,
        # Request-path writers that do not pass the IP still get the one
        # LatencyMiddleware bound (from ``get_client_ip``); jobs have none.
        ip_address=ip_address or current_client_ip(),
    )
    session.add(row)
    if is_incident(action, detail):
        # Publish only once the row is visible to other connections.
        event.listen(
            session.sync_session,
            "after_commit",
            lambda _s: notify_incidents([row]),
            once=True,
        )
    if commit:
        await session.commit()


def is_incident(action: str, detail: Any) -> bool:
    """CRITICAL / ALERT rows wake the live governance stream on every replica."""
    return level_for(detail if isinstance(detail, dict) else {}, action) in (
        INCIDENT_LEVELS
    )


def notify_incidents(rows: list[AuditLog]) -> None:
    """NOTIFY (via the event bus) for each committed incident row; never raises."""
    for row in rows:
        if row.id is None or not is_incident(row.action, row.detail):
            continue
        try:
            spawn(
                publish_event(TOPIC_GOVERNANCE_INCIDENT, row.id),
                name="governance_incident_notify",
            )
        except RuntimeError:
            logger.debug("incident_notify_skipped_no_loop")


def mask_detail(value: Any) -> Any:
    """Recursively PII-redact (and length-cap) every string in an audit detail."""
    if isinstance(value, str):
        return redact_pii(value[:_MAX_DETAIL_STR])[0]
    if isinstance(value, dict):
        return {str(k): mask_detail(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [mask_detail(v) for v in value]
    return value


def current_client_ip() -> str | None:
    """The client IP bound by LatencyMiddleware, or None outside a request."""
    value = structlog.contextvars.get_contextvars().get("client_ip")
    return str(value)[:64] if value else None


def current_request_id() -> str:
    """The request id bound by LatencyMiddleware, or ``"system"`` outside a request."""
    return str(
        structlog.contextvars.get_contextvars().get("request_id") or SYSTEM_REQUEST_ID
    )[:64]


async def _write_batch(rows: list[dict[str, Any]]) -> None:
    async with AsyncSessionLocal() as session:
        written = [AuditLog(**row) for row in rows]
        session.add_all(written)
        await session.commit()
    notify_incidents(written)


# Indirection so tests can capture batches without a database.
_batch_writer: Callable[[list[dict[str, Any]]], Awaitable[None]] = _write_batch


async def _flush_loop() -> None:
    # Yield once so events emitted in the same tick land in one batch.
    await asyncio.sleep(0)
    while _queue:
        batch = _queue[:]
        _queue.clear()
        try:
            await _batch_writer(batch)
        except Exception:
            logger.warning(
                "audit_batch_write_failed",
                rows=len(batch),
                actions=sorted({r["action"] for r in batch}),
                exc_info=True,
            )


def _ensure_flusher() -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("audit_event_dropped_no_loop", queued=len(_queue))
        _queue.clear()
        return
    if (
        _Flusher.task is None
        or _Flusher.task.done()
        or _Flusher.task.get_loop() is not loop
    ):
        _Flusher.task = spawn(_flush_loop(), name="audit_flush")


def emit_audit_event(
    action: str,
    detail: dict | None = None,
    *,
    request_id: str | None = None,
    actor: str | None = None,
    ip_address: str | None = None,
) -> None:
    """Queue an insert-only audit row without blocking the caller.

    Missing request_id / actor / ip are filled from the request context bound
    by LatencyMiddleware. ``detail`` is PII-masked before it is queued.
    """
    if len(_queue) >= _MAX_QUEUE:
        logger.warning("audit_queue_full_event_dropped", action=action)
        return
    ctx = structlog.contextvars.get_contextvars()
    _queue.append(
        {
            "request_id": str(request_id or ctx.get("request_id") or SYSTEM_REQUEST_ID)[
                :64
            ],
            "actor": str(actor or ctx.get("actor") or "system")[:256],
            "action": action[:64],
            "detail": mask_detail(detail or {}),
            "ip_address": ip_address or current_client_ip(),
        }
    )
    _ensure_flusher()


async def flush_audit_events() -> None:
    """Wait until every queued event has been written (tests and shutdown)."""
    loop = asyncio.get_running_loop()
    while True:
        task = _Flusher.task
        if task is not None and not task.done() and task.get_loop() is loop:
            await task
        elif _queue:
            _ensure_flusher()
        else:
            return


def record_server_error(
    request_id: str,
    path: str,
    status_code: int = 500,
    error_type: str | None = None,
    ip_address: str | None = None,
) -> None:
    """Audit one 5xx as a ``server_error`` row (non-blocking).

    LatencyMiddleware calls this for 5xx *responses*; the global exception
    handler (app.core.exceptions) calls it for unhandled exceptions, so each
    5xx is exactly one row.
    """
    detail: dict[str, Any] = {"path": path, "status_code": status_code}
    if error_type:
        detail["error_type"] = error_type
    emit_audit_event(
        "server_error", detail, request_id=request_id, ip_address=ip_address
    )
