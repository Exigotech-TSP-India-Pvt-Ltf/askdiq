"""Views of the precomputed Evaluation (live) snapshot for the Evaluation page.

``GET /metrics/evaluation/live`` returns the whole report, every case with
its answer and retrieved chunks (hundreds of KB). The page needs far less
per view, so it reads small derived views of the same in-memory payload:

- *summary*: the report without its case lists, plus the pass / fail split
  the outcome chart draws (same rule the page used: known issues excluded);
- *cases*: one filtered, searched page of slim explorer rows;
- *case*: one full case for the record drawer.

Every view is a pure function of the snapshot payload, so its numbers are the
report's numbers. Encoded bodies are cached per payload object (a recomputed
snapshot is a new object), so a repeat read costs a dict lookup and a
matching ``If-None-Match`` is a 304 without any work.
"""

from __future__ import annotations

import asyncio
import json
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from app.core.concurrency import single_flight
from app.core.http_cache import weak_etag
from app.core.logging_config import get_logger
from app.dashboard.snapshots import Gather
from app.dashboard.window import utcnow
from app.models.db_models import EvaluationRecord

logger = get_logger(__name__)

CASE_LISTS = ("cases", "failed_cases")

# Explorer row fields (what the table renders and searches, nothing else).
ROW_FIELDS = (
    "id",
    "request_id",
    "type",
    "query",
    "passed",
    "known_issue",
    "grounded",
    "confidence",
    "recall",
    "citation_precision",
    "faithfulness",
    "latency_ms",
    "created_at",
)
# Fields the explorer search matches (case-insensitive substring).
SEARCH_FIELDS = ("query", "answer", "id", "type")

OUTCOME_FILTERS = ("all", "passed", "failed")
GROUNDED_FILTERS = ("all", "grounded", "ungrounded")

_MAX_VIEWS = 1024


def outcome_split(cases: list[dict[str, Any]]) -> dict[str, int]:
    """Scored cases passed / failed (known issues are neither)."""
    passed = failed = 0
    for case in cases:
        if case.get("known_issue"):
            continue
        if case.get("passed"):
            passed += 1
        else:
            failed += 1
    return {"passed": passed, "failed": failed}


def summary_view(payload: dict[str, Any]) -> dict[str, Any]:
    """The report without its case lists, plus the outcome split."""
    out = {k: v for k, v in payload.items() if k not in CASE_LISTS}
    out["outcomes"] = outcome_split(payload.get("cases") or [])
    return out


def _row(case: dict[str, Any]) -> dict[str, Any]:
    return {field: case.get(field) for field in ROW_FIELDS}


def _matches(case: dict[str, Any], outcome: str, grounded: str, needle: str) -> bool:
    if outcome == "passed" and not case.get("passed"):
        return False
    if outcome == "failed" and case.get("passed"):
        return False
    if grounded == "grounded" and not case.get("grounded"):
        return False
    if grounded == "ungrounded" and case.get("grounded"):
        return False
    if needle:
        return any(
            needle in str(case.get(field) or "").lower() for field in SEARCH_FIELDS
        )
    return True


def cases_view(  # pylint: disable=too-many-arguments
    payload: dict[str, Any],
    *,
    outcome: str = "all",
    grounded: str = "all",
    q: str = "",
    offset: int = 0,
    limit: int = 25,
) -> dict[str, Any]:
    """One page of explorer rows (report order) and the filtered total."""
    needle = q.strip().lower()
    matched = [
        case
        for case in payload.get("cases") or []
        if _matches(case, outcome, grounded, needle)
    ]
    total = len(matched)
    # A page past the end shows the last page (as the table always did).
    last_offset = max(0, ((total - 1) // limit) * limit) if total else 0
    offset = min(offset, last_offset)
    return {
        "items": [_row(case) for case in matched[offset : offset + limit]],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


def find_case(payload: dict[str, Any], case_id: str) -> dict[str, Any] | None:
    """The full case with ``case_id`` (id or request id), if the report has it."""
    for case in payload.get("cases") or []:
        if case.get("id") == case_id or case.get("request_id") == case_id:
            return case
    return None


class ViewCache:
    """Encoded views per (snapshot key, view arguments), valid while the
    snapshot payload object is the same one."""

    def __init__(self, max_size: int = _MAX_VIEWS) -> None:
        self._max_size = max_size
        self._store: OrderedDict[tuple[str, ...], tuple[Any, bytes, str]] = (
            OrderedDict()
        )

    def get(
        self,
        key: tuple[str, ...],
        payload: Any,
        build: Callable[[], Any],
    ) -> tuple[bytes, str]:
        hit = self._store.get(key)
        if hit is not None and hit[0] is payload:
            self._store.move_to_end(key)
            return hit[1], hit[2]
        body = json.dumps(
            build(), ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
        etag = weak_etag(body)
        self._store[key] = (payload, body, etag)
        self._store.move_to_end(key)
        while len(self._store) > self._max_size:
            self._store.popitem(last=False)
        return body, etag

    def clear(self) -> None:
        self._store.clear()


view_cache = ViewCache()


# ---------------------------------------------------------------------------
# Real time
# ---------------------------------------------------------------------------
# The console refetches the page's views as soon as new data is seen (``data``
# scope, every DASHBOARD_WATCH_SECONDS). A view whose payload predates the
# newest evaluation then recomputes it in the background at once (one build,
# ~100 ms) instead of waiting for the refresher's next cycle; the recompute
# announces itself, so the page refetches the new numbers. The newest
# evaluation time is read on every such view read (an index-only max, ~1 ms):
# a cached one could predate the very change the refetch is about.

_rebuilds: set[asyncio.Task[Any]] = set()


def payload_may_be_behind(params: dict[str, Any], as_of: datetime | None) -> bool:
    """A computed payload whose window reaches today."""
    if as_of is None:
        return False
    last = params.get("to")
    return not last or last >= utcnow().date().isoformat()


async def newest_evaluation(gather: Gather) -> datetime | None:
    """Creation time of the newest evaluation record."""
    (value,) = await gather(
        lambda db: db.scalar(select(func.max(EvaluationRecord.created_at)))
    )
    return value


# A payload rebuilt this recently is not rebuilt again, and evaluation times
# in the future (clock skew, bad rows) never trigger one: no rebuild loops.
MIN_REBUILD_AGE = timedelta(seconds=1)
MAX_CLOCK_SKEW = timedelta(seconds=60)


def is_after(created_at: datetime, as_of: datetime) -> bool:
    """``created_at`` (naive UTC in the table) is a real evaluation later
    than a payload computed at ``as_of`` (at least MIN_REBUILD_AGE ago)."""
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    now = datetime.now(UTC)
    if now - as_of < MIN_REBUILD_AGE or created_at > now + MAX_CLOCK_SKEW:
        return False
    return created_at > as_of


def rebuild_in_background(key: str, build: Callable[[], Awaitable[Any]]) -> None:
    """One background rebuild per snapshot key at a time."""
    task = asyncio.create_task(single_flight.run(f"snapshot-build:{key}", build))
    _rebuilds.add(task)
    task.add_done_callback(_rebuild_done)


def _rebuild_done(task: asyncio.Task[Any]) -> None:
    _rebuilds.discard(task)
    if not task.cancelled() and task.exception() is not None:
        logger.warning("live_eval_rebuild_failed", error=str(task.exception()))
