"""Seeders and an SSE reader shared by the governance endpoint tests.

Every seeder writes inside the caller's rolled-back ``db_session``. Rows get
timestamps in a per-test window far in the past (``unique_window``) so
``from`` / ``to`` filters isolate them from rows other tests committed
(``audit_log`` is append-only and cannot be cleaned up).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import get_settings
from app.models.db_models import AuditLog, ChatMessageRecord, ChatSession, LatencyRecord

# action, detail -> expected level / service (covers every level).
LEVEL_FIXTURES: tuple[tuple[str, dict[str, Any], str, str], ...] = (
    ("query", {"guardrail_flags": ["llm_call_failed"]}, "CRITICAL", "query"),
    ("negative_feedback_spike", {"negative_rate": 0.5}, "ALERT", "feedback"),
    ("server_error", {"path": "/query", "status_code": 500}, "ERROR", "system"),
    ("rate_limited", {"scope": "per IP"}, "WARNING", "system"),
    ("ingest", {"source_name": "a.md"}, "INFO", "ingest"),
)


def unique_window(days: int = 2) -> tuple[datetime, datetime]:
    """A random 2-day window in 2001-2010, unique per test run."""
    offset = uuid.uuid4().int % (365 * 9)
    start = datetime(2001, 1, 1) + timedelta(days=offset)
    return start, start + timedelta(days=days)


def iso(dt: datetime) -> str:
    return dt.isoformat()


def audit_rows(
    start: datetime,
    count: int,
    *,
    fixtures: Iterable[tuple[str, dict[str, Any], str, str]] = LEVEL_FIXTURES,
    step: timedelta = timedelta(seconds=30),
    detail_extra: dict[str, Any] | None = None,
    same_timestamp_every: int = 0,
) -> list[AuditLog]:
    """``count`` rows cycling through ``fixtures``; optional timestamp ties."""
    fixtures = list(fixtures)
    rows = []
    for i in range(count):
        action, detail, _, _ = fixtures[i % len(fixtures)]
        tick = i // same_timestamp_every if same_timestamp_every else i
        rows.append(
            AuditLog(
                id=uuid.uuid4(),
                created_at=start + step * tick,
                request_id=f"gl-{i}",
                actor="gov-tests@example.com",
                action=action,
                detail={**detail, **(detail_extra or {}), "seq": i},
                ip_address="198.51.100.7",
            )
        )
    return rows


async def chat_session(db: AsyncSession, ip: str | None = None) -> ChatSession:
    now = datetime.utcnow()
    session = ChatSession(
        id=uuid.uuid4(),
        user_email="gov-tests@example.com",
        title="t",
        created_at=now,
        last_activity_at=now,
        expires_at=now + timedelta(hours=1),
        created_ip=ip,
    )
    db.add(session)
    await db.flush()
    return session


def trace_rows(
    session: ChatSession,
    created_at: datetime,
    *,
    total_ms: float,
    outcome: str | None,
    flags: list[str] | None,
    with_turn: bool = True,
    ip: str = "198.51.100.7",
    query: str = "what is the policy",
    stages: dict[str, float | None] | None = None,
    sources: list[dict[str, Any]] | None = None,
    tokens: tuple[int, int] = (100, 20),
    cost: float = 0.0012,
) -> tuple[str, list[Any]]:
    """One trace: latency row + optional assistant turn + query audit row."""
    request_id = f"gt-{uuid.uuid4().hex[:20]}"
    rows: list[Any] = [
        LatencyRecord(
            id=uuid.uuid4(),
            request_id=request_id,
            created_at=created_at,
            total_ms=total_ms,
            **{f"{k}_ms": v for k, v in (stages or {}).items()},
        ),
        AuditLog(
            request_id=request_id,
            created_at=created_at,
            actor="gov-tests@example.com",
            action="query",
            detail={
                "query": query,
                "guardrail_flags": flags or [],
                "outcome": outcome,
                "source_ids": [s["chunk_id"] for s in sources or []],
                "sources": sources or [],
            },
            ip_address=ip,
        ),
    ]
    if with_turn:
        rows.append(
            ChatMessageRecord(
                session_id=session.id,
                turn_index=0,
                role="assistant",
                content="answer",
                created_at=created_at,
                request_id=request_id,
                outcome=outcome,
                guardrail_flags=flags,
                grounded=outcome == "answered",
                ip_address=ip,
                prompt_tokens=tokens[0],
                completion_tokens=tokens[1],
                total_tokens=sum(tokens),
                cost_usd=cost,
            )
        )
    return request_id, rows


class QueryCounter:
    """Counts SELECT statements on an engine (SQLAlchemy cursor events)."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine.sync_engine
        self.statements: list[str] = []

    def _on_execute(self, _conn, _cursor, statement, *_args) -> None:
        if statement.lstrip().upper().startswith(("SELECT", "WITH")):
            self.statements.append(statement)

    def __enter__(self) -> QueryCounter:
        event.listen(self.engine, "before_cursor_execute", self._on_execute)
        return self

    def __exit__(self, *_exc: object) -> None:
        event.remove(self.engine, "before_cursor_execute", self._on_execute)

    @property
    def count(self) -> int:
        return len(self.statements)


async def read_sse(
    app: Any, url: str, frames: int, wait_seconds: float = 10.0
) -> tuple[dict[str, Any], str]:
    """Call ``app`` over raw ASGI, collect ``frames`` SSE frames, then disconnect.

    httpx's ASGITransport buffers the whole body, which never ends for SSE.
    """
    parts = urlsplit(url)
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": parts.path,
        "raw_path": parts.path.encode(),
        "query_string": parts.query.encode(),
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"x-api-key", (get_settings().api_key_name or "").encode()),
            (b"accept", b"text/event-stream"),
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }
    done = asyncio.Event()
    state: dict[str, Any] = {"sent_request": False, "start": {}, "body": ""}

    async def receive() -> dict[str, Any]:
        if not state["sent_request"]:
            state["sent_request"] = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await done.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            state["start"] = message
        elif message["type"] == "http.response.body":
            state["body"] += message.get("body", b"").decode()
            if state["body"].count("\n\n") >= frames or not message.get(
                "more_body", False
            ):
                done.set()

    task = asyncio.create_task(app(scope, receive, send))
    try:
        await asyncio.wait_for(done.wait(), wait_seconds)
    finally:
        done.set()
        try:
            await asyncio.wait_for(task, 5)
        except (TimeoutError, asyncio.CancelledError):
            task.cancel()
    return state["start"], state["body"]


def parse_frames(body: str) -> list[dict[str, str]]:
    frames = []
    for raw in body.split("\n\n"):
        if not raw.strip():
            continue
        frame: dict[str, str] = {}
        for line in raw.splitlines():
            key, _, value = line.partition(": ")
            if key == "data":
                frame["data"] = frame.get("data", "") + value
            else:
                frame[key] = value
        frames.append(frame)
    return frames
