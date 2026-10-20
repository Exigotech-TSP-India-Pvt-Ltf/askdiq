"""Query builders of the Usage & Cost lists (All Users / All Sessions / All
Queries tabs and the CSV export).

Every list reads ``usage_turns`` (``UsageTurn``): one narrow row per user turn
paired with its assistant answer, kept in step by database triggers.

- All Users / All Sessions: one aggregate per (audience, date range) holds
  every IP / session row of that range (:func:`users_base`,
  :func:`sessions_base`, built by Postgres as one JSON value); search, type
  and IP filters, sorting, header stats and paging are applied to it in
  memory (:func:`users_page`, :func:`sessions_page`), so they never
  re-aggregate the history.
- All Queries: filters on the ledger (:func:`turn_filters`) page through the
  sort indexes; texts and titles are joined for the rows on screen only.
"""

from __future__ import annotations

import contextlib
import heapq
import json
import uuid
from collections.abc import Callable, Sequence
from datetime import datetime
from operator import itemgetter
from typing import Any

from fastapi import HTTPException
from sqlalchemy import (
    ColumnElement,
    String,
    Text,
    case,
    cast,
    func,
    or_,
    select,
    text,
    tuple_,
    union,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.governance.audience import Audience, build_audience_filter, get_classified_ips
from app.models.db_models import ChatMessageRecord, ChatSession, UsageTurn

T = UsageTurn

# The client IP of a turn: the user message's, else the session's.
CLIENT_IP = func.coalesce(T.user_ip, T.session_ip)
# Visible output tokens of a turn (completion minus reasoning, never negative).
OUTPUT_TOKENS = func.greatest(0, T.completion_tokens - T.reasoning_tokens)
# A single IP above this many queries likely fronts a shared gateway/NAT.
SHARED_GATEWAY_QUERY_THRESHOLD = 50
# Highlight cards above the Users / Sessions tables.
TOP_CARDS = 3
UNKNOWN_IP = "Unknown"

# Token / cost totals carried by the per-range rows, in this order.
_TOTALS = (
    "queries",
    "embed_tokens",
    "prompt_tokens",
    "reasoning_tokens",
    "output_tokens",
    "total_tokens",
    "cost_usd",
)


def escape_ilike_pattern(text_value: str) -> str:
    """Escape special characters in user search inputs for SQL ILIKE queries."""
    if not text_value:
        return ""
    if "\x00" in text_value:
        raise HTTPException(
            status_code=422, detail="Search query contains prohibited null characters."
        )
    # Escape backslash first, then wildcard percent and underscore
    return text_value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _needle(search: str | None) -> str | None:
    """Case-folded search box value (None when blank); validated like ILIKE input."""
    if not search or not search.strip():
        return None
    escape_ilike_pattern(search)  # rejects NUL like the SQL searches
    return search.strip().lower()


def _contains(value: str | None, needle: str) -> bool:
    """``value ILIKE '%needle%'`` (literal text; NULL never matches)."""
    return value is not None and needle in value.lower()


def _ilike(column: Any, pattern: str) -> ColumnElement[bool]:
    return cast(column, String).ilike(pattern, escape="\\")


def in_range(lo: datetime | None, hi: datetime | None) -> list[ColumnElement[bool]]:
    """Turns asked inside ``[lo, hi]`` (either end optional)."""
    conds: list[ColumnElement[bool]] = []
    if lo is not None:
        conds.append(T.created_at >= lo)
    if hi is not None:
        conds.append(T.created_at <= hi)
    return conds


def _turn_totals() -> list[Any]:
    """Per-group token / cost totals of ledger rows (labels of ``_TOTALS``)."""
    return [
        func.count().label("queries"),
        func.coalesce(func.sum(T.embedding_tokens), 0).label("embed_tokens"),
        func.coalesce(func.sum(T.prompt_tokens), 0).label("prompt_tokens"),
        func.coalesce(func.sum(T.reasoning_tokens), 0).label("reasoning_tokens"),
        func.coalesce(func.sum(OUTPUT_TOKENS), 0).label("output_tokens"),
        func.coalesce(func.sum(T.total_tokens), 0).label("total_tokens"),
        func.coalesce(func.sum(T.cost_usd), 0.0).label("cost_usd"),
    ]


def _iso_text(ts: Any) -> Any:
    """``ts`` as Python's ``isoformat() + "Z"`` (a fraction only when it is not
    zero); NULL stays NULL."""
    micros = func.to_char(ts, "US", type_=String)
    fraction = case((micros == "000000", ""), else_="." + micros)
    return func.to_char(ts, 'YYYY-MM-DD"T"HH24:MI:SS', type_=String) + fraction + "Z"


async def _json_rows(db: AsyncSession, stmt: Any, *columns: str) -> list[list[Any]]:
    """Rows of ``stmt`` as JSON arrays of ``columns``, built by Postgres in one
    value (no per-row driver / Python objects), costs rounded as shown."""
    sub = stmt.subquery("rows")
    agg = func.coalesce(
        func.json_agg(func.json_build_array(*(sub.c[name] for name in columns))),
        text("'[]'::json"),
    )
    raw = await db.scalar(select(cast(agg, Text)))
    rows: list[list[Any]] = json.loads(raw or "[]")
    cost = columns.index("cost_usd")
    for row in rows:
        row[cost] = round(float(row[cost]), 6)
    return rows


def _sort_key(index: int, *, text_key: bool) -> Callable[[Sequence[Any]], Any]:
    if text_key:
        # Same-format ISO-8601 strings sort chronologically; none sorts first.
        return lambda r: r[index] or ""
    return lambda r: r[index] if r[index] is not None else 0


# ---------------------------------------------------------------------------
# All Users
# ---------------------------------------------------------------------------

# Row layout of :func:`users_base` (a JSON array per IP).
USER_COLUMNS = ("ip_address", "is_test", "sessions", *_TOTALS, "last_active")
_U = {name: i for i, name in enumerate(USER_COLUMNS)}


async def users_base(
    db: AsyncSession, audience: Audience, lo: datetime | None, hi: datetime | None
) -> list[list[Any]]:
    """Every client IP with turns in ``[lo, hi]`` (audience on the turn's own
    IP): test flag, sessions, totals, last turn; ordered by IP."""
    test_ips, _, _ = await get_classified_ips(db)
    ip_col = func.coalesce(T.user_ip, UNKNOWN_IP)
    conds = in_range(lo, hi)
    aud = await build_audience_filter(db, T.user_ip, audience)
    if aud is not None:
        conds.append(aud)
    # Per (IP, session) first: the IP's session count is then a plain count
    # (a hash aggregate, no DISTINCT sort over the turns).
    per_session = (
        select(
            ip_col.label("ip"),
            T.session_id,
            *_turn_totals(),
            func.max(T.created_at).label("last_active"),
        )
        .where(*conds)
        .group_by(ip_col, T.session_id)
        .subquery("per_session")
    )
    c = per_session.c
    per_ip = (
        select(
            c.ip.label("ip_address"),
            or_(c.ip.in_(sorted(test_ips)), c.ip == UNKNOWN_IP).label("is_test"),
            func.count().label("sessions"),
            *(func.sum(c[name]).label(name) for name in _TOTALS),
            _iso_text(func.max(c.last_active)).label("last_active"),
        )
        .group_by(c.ip)
        .order_by(c.ip)
    )
    return await _json_rows(db, per_ip, *USER_COLUMNS)


def _user_item(row: Sequence[Any]) -> dict[str, Any]:
    item = dict(zip(USER_COLUMNS, row, strict=True))
    item["type"] = "Test" if item["is_test"] else "Production"
    return item


def _user_sort(sort: str) -> Callable[[Sequence[Any]], Any]:
    if sort.startswith("last_active"):
        return _sort_key(_U["last_active"], text_key=True)
    if "queries" in sort:
        name = "queries"
    elif "sessions" in sort:
        name = "sessions"
    elif "tokens" in sort:
        name = "total_tokens"
    else:
        name = "cost_usd"
    return _sort_key(_U[name], text_key=False)


def _kept_types(
    rows: list[Sequence[Any]], user_type: str | None
) -> list[Sequence[Any]]:
    """The "User Type" filter (any value but test / production keeps none)."""
    if not user_type:
        return rows
    kind = user_type.lower()
    if kind not in ("test", "production"):
        return []
    want_test = kind == "test"
    return [r for r in rows if bool(r[_U["is_test"]]) is want_test]


def users_page(
    rows: Sequence[Sequence[Any]],
    *,
    q: str | None,
    user_type: str | None,
    sort: str,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """One All Users page (and header stats) from :func:`users_base` rows."""
    needle = _needle(q)
    matched = (
        list(rows)
        if needle is None
        else [r for r in rows if r[0] != UNKNOWN_IP and _contains(r[0], needle)]
    )
    tokens, queries = _U["total_tokens"], _U["queries"]
    total_tokens_all = sum(r[tokens] for r in matched) or 1
    kept = sorted(
        _kept_types(matched, user_type),
        key=_user_sort(sort),
        reverse="asc" not in sort,
    )
    top_3 = [_user_item(r) for r in kept[:TOP_CARDS]]
    top_tokens = sum(x["total_tokens"] for x in top_3)
    return {
        "items": [_user_item(r) for r in kept[offset : offset + limit]],
        "total": len(kept),
        "limit": limit,
        "offset": offset,
        "stats": {
            "unique_users": len(kept),
            "active_sessions": sum(r[_U["sessions"]] for r in kept),
            "queries_driven": sum(r[queries] for r in kept),
            "top_consumer_share_pct": round(top_tokens / total_tokens_all * 100, 1),
            "top_users": [
                {
                    "ip_address": x["ip_address"],
                    "queries": x["queries"],
                    "tokens": x["total_tokens"],
                    "cost_usd": x["cost_usd"],
                    "token_share_pct": round(
                        x["total_tokens"] / total_tokens_all * 100, 1
                    ),
                }
                for x in top_3
            ],
            "shared_gateway_hint": any(
                r[queries] > SHARED_GATEWAY_QUERY_THRESHOLD for r in kept
            ),
        },
    }


# ---------------------------------------------------------------------------
# All Sessions
# ---------------------------------------------------------------------------

# Row layout of :func:`sessions_base` (a JSON array per session).
SESSION_COLUMNS = ("id", "title", "ip_address", "last_active", *_TOTALS)
_S = {name: i for i, name in enumerate(SESSION_COLUMNS)}


async def sessions_base(
    db: AsyncSession, audience: Audience, lo: datetime | None, hi: datetime | None
) -> list[list[Any]]:
    """Every session with turns in ``[lo, hi]`` (audience on the session's
    creating IP) with the totals of those turns; ordered by session id."""
    conds = in_range(lo, hi)
    # ``usage_turns.session_ip`` mirrors the session's ``created_ip``.
    aud = await build_audience_filter(db, T.session_ip, audience)
    if aud is not None:
        conds.append(aud)
    per_session = (
        select(T.session_id, *_turn_totals())
        .where(*conds)
        .group_by(T.session_id)
        .subquery("per_session")
    )
    stmt = (
        select(
            cast(ChatSession.id, Text).label("id"),
            ChatSession.title.label("title"),
            ChatSession.created_ip.label("ip_address"),
            _iso_text(ChatSession.last_activity_at).label("last_active"),
            *(per_session.c[name] for name in _TOTALS),
        )
        .join(per_session, per_session.c.session_id == ChatSession.id)
        .order_by(ChatSession.id)
    )
    return await _json_rows(db, stmt, *SESSION_COLUMNS)


def _session_item(row: Sequence[Any]) -> dict[str, Any]:
    item = dict(zip(SESSION_COLUMNS, row, strict=True))
    item["ip_address"] = item["ip_address"] or UNKNOWN_IP
    return item


def _session_sort(sort: str) -> Callable[[Sequence[Any]], Any]:
    if "cost" in sort:
        return _sort_key(_S["cost_usd"], text_key=False)
    if "queries" in sort:
        return _sort_key(_S["queries"], text_key=False)
    if "tokens" in sort:
        return _sort_key(_S["total_tokens"], text_key=False)
    return _sort_key(_S["last_active"], text_key=True)


def _session_matches(row: Sequence[Any], needle: str) -> bool:
    return (
        _contains(row[_S["title"]], needle)
        or _contains(row[_S["id"]], needle)
        or _contains(row[_S["ip_address"]], needle)
    )


def sessions_page(
    rows: Sequence[Sequence[Any]],
    *,
    q: str | None,
    ip: str | None,
    sort: str,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """One All Sessions page (and header stats) from :func:`sessions_base` rows."""
    needle = _needle(q)
    exact_ip = ip.strip() if ip and ip.strip() else None
    ip_col = _S["ip_address"]
    kept = [
        r
        for r in rows
        if (exact_ip is None or r[ip_col] == exact_ip)
        and (needle is None or _session_matches(r, needle))
    ]
    kept.sort(key=_session_sort(sort), reverse="asc" not in sort)

    total = len(kept)
    cost, queries = _S["cost_usd"], _S["queries"]
    # nlargest = sorted(..., reverse=True)[:n] (stable on the current order).
    top_3 = [
        _session_item(r) for r in heapq.nlargest(TOP_CARDS, kept, key=itemgetter(cost))
    ]
    return {
        "items": [_session_item(r) for r in kept[offset : offset + limit]],
        "total": total,
        "limit": limit,
        "offset": offset,
        "stats": {
            "total_sessions": total,
            "avg_turns_per_session": round(sum(r[queries] for r in kept) / total, 1)
            if total
            else 0,
            "avg_cost_per_session": round(sum(r[cost] for r in kept) / total, 6)
            if total
            else 0.0,
            "max_depth": max((r[queries] for r in kept), default=0),
            "top_sessions": [
                {
                    "id": x["id"],
                    "title": x["title"],
                    "ip_address": x["ip_address"],
                    "queries": x["queries"],
                    "tokens": x["total_tokens"],
                    "cost_usd": x["cost_usd"],
                }
                for x in top_3
            ],
        },
    }


# ---------------------------------------------------------------------------
# All Queries
# ---------------------------------------------------------------------------


def _text_search(pattern: str) -> ColumnElement[bool]:
    """Question or answer text, session title, user IP or request id contains
    ``pattern``: a union of trigram / key index lookups (no per-turn scan)."""
    messages = select(ChatMessageRecord.id).where(
        _ilike(ChatMessageRecord.content, pattern)
    )
    sessions = select(ChatSession.id).where(_ilike(ChatSession.title, pattern))
    pair = (T.asst_msg_id, T.user_msg_id)
    matching = union(
        select(*pair).where(T.user_msg_id.in_(messages)),
        select(*pair).where(T.asst_msg_id.in_(messages)),
        select(*pair).where(T.session_id.in_(sessions)),
        select(*pair).where(_ilike(T.user_ip, pattern)),
        select(*pair).where(_ilike(T.request_id, pattern)),
    )
    return tuple_(*pair).in_(matching)


async def turn_filters(
    db: AsyncSession,
    audience: Audience,
    *,
    lo: datetime | None = None,
    hi: datetime | None = None,
    ip: str | None = None,
    session_id: str | None = None,
    outcome: str | None = None,
    grounded: bool | None = None,
    search: str | None = None,
) -> list[ColumnElement[bool]]:
    """Conditions on ``usage_turns`` for the All Queries tab and its export."""
    conds = in_range(lo, hi)
    aud = await build_audience_filter(db, CLIENT_IP, audience)
    if aud is not None:
        conds.append(aud)
    if ip:
        conds.append(ip.strip() == CLIENT_IP)
    if session_id:
        with contextlib.suppress(ValueError):
            conds.append(T.session_id == uuid.UUID(session_id.strip()))
    if outcome:
        conds.append(T.outcome == outcome.strip().lower())
    if grounded is not None:
        conds.append(T.grounded == grounded)
    if search and search.strip():
        conds.append(_text_search(f"%{escape_ilike_pattern(search.strip())}%"))
    return conds


_CUSTOM_PLANS = text("SET LOCAL plan_cache_mode = force_custom_plan")


async def force_custom_plans(db: AsyncSession) -> None:
    """Plan this transaction's statements for their actual values.

    The driver re-executes prepared statements; after a few runs Postgres may
    switch to a generic plan that cannot see the offset, limit or date range
    (a page at offset 10 000 then sorted every matching turn)."""
    await db.execute(_CUSTOM_PLANS)
