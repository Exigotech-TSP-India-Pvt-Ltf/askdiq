"""Dashboard load-speed regressions (perf/dashboard-load-speed).

Cheap, deterministic guards on the shape of the hot admin queries (no
per-row ``jsonb`` re-parse, no lateral joins in an unfiltered count), the
usage caches (permission-scoped keys, per-key single flight) and, against
Postgres (CI), parity of the SQL rewrites with the Python rules they replace.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import literal_column, select, text
from sqlalchemy.dialects import postgresql

from app.api.routes import admin as admin_routes
from app.api.routes import admin_usage
from app.auth.permissions import Permission
from app.core.cache import aggregation_cache
from app.dashboard import snapshots
from app.dashboard.queries import ASST, cached_tokens_sql
from app.governance import logs, traces
from app.models.db_models import ChatMessageRecord, Chunk, LatencyRecord
from tests.dashboard_helpers import exchange, session_at, unique_ip


def _sql(clause: object) -> str:
    return str(
        clause.compile(  # type: ignore[attr-defined]
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


# ---------------------------------------------------------------------------
# Query shape (no database)
# ---------------------------------------------------------------------------


def test_cached_tokens_reads_the_json_key_without_a_jsonb_cast() -> None:
    sql = _sql(cached_tokens_sql(ASST))
    assert "json_typeof" in sql
    assert "JSONB" not in sql.upper().replace("JSON_TYPEOF", "")


def test_log_level_casts_only_the_small_values_not_the_whole_detail() -> None:
    sql = _sql(logs.level_sql())
    assert "CAST(audit_log.detail AS JSONB)" not in sql
    assert "audit_log.detail -> 'guardrail_flags'" in sql


def test_log_level_accepts_precomputed_columns() -> None:
    sql = _sql(
        logs.level_sql(logs.flags_sql(), logs.grounded_sql(), literal_column("act"))
    )
    assert "act IN" in sql


@pytest.mark.parametrize(
    ("filters", "needs"),
    [
        (traces.TraceFilters(), False),
        (traces.TraceFilters(date_from=datetime(2026, 1, 1)), False),
        (traces.TraceFilters(latency_buckets=("<1s",)), False),
        (traces.TraceFilters(audience="real"), True),
        (traces.TraceFilters(statuses=("ok",)), True),
        (traces.TraceFilters(outcomes=("answered",)), True),
        (traces.TraceFilters(q="x"), True),
    ],
)
def test_trace_count_joins_laterals_only_when_a_filter_reads_them(
    filters: traces.TraceFilters, needs: bool
) -> None:
    assert traces._needs_laterals(filters) is needs


async def test_unfiltered_trace_count_scans_latency_metrics_alone() -> None:
    f = traces.TraceFilters()
    # audience "overview" never touches the session.
    stmt = await traces._base(None, f, LatencyRecord.id, laterals=False)  # type: ignore[arg-type]
    sql = _sql(stmt).upper()
    assert "LATERAL" not in sql
    assert "CHAT_MESSAGES" not in sql
    full = _sql(await traces._base(None, f, LatencyRecord.id)).upper()  # type: ignore[arg-type]
    assert "LATERAL" in full


# ---------------------------------------------------------------------------
# Cache scope and single flight (no database)
# ---------------------------------------------------------------------------


def test_usage_cache_keys_carry_the_permission_scope() -> None:
    scope = Permission.DASHBOARD_READ.value
    assert scope == admin_usage._SUMMARY_CACHE_SCOPE
    key = admin_routes._usage_card_key("llm")
    assert key.startswith(admin_usage.USAGE_CARD_CACHE_PREFIX)
    assert scope in key


def test_clear_usage_caches_drops_usage_cards() -> None:
    key = admin_routes._usage_card_key("llm")
    aggregation_cache.set(key, {"cost": "$1"})
    admin_usage.clear_usage_caches()
    assert aggregation_cache.get(key) is None


async def test_summary_keys_compute_concurrently_and_identical_requests_share_one_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin_usage.clear_usage_caches()
    release = asyncio.Event()
    started: list[str] = []

    async def fake_compute(_runner, audience, *_args):
        started.append(audience)
        await release.wait()
        return {"audience": audience}

    monkeypatch.setattr(admin_usage, "_compute_usage_summary", fake_compute)
    # No database: snapshot rows live in a dict, tasks get no session.
    rows: dict[str, object] = {}

    async def read_row(_session, key):
        return rows.get(key)

    async def write_row(_session, values):
        rows[values["key"]] = values

    monkeypatch.setattr(snapshots, "_read_row", read_row)
    monkeypatch.setattr(snapshots, "_write_row", write_row)

    class NoDbRunner:
        @staticmethod
        async def gather(*tasks):
            return [await task(None) for task in tasks]

    runner = NoDbRunner()
    calls = [
        admin_usage._usage_summary_payload("overview", None, None, runner),  # type: ignore[arg-type]
        admin_usage._usage_summary_payload("overview", None, None, runner),  # type: ignore[arg-type]
        admin_usage._usage_summary_payload("real", None, None, runner),  # type: ignore[arg-type]
    ]
    tasks = [asyncio.create_task(c) for c in calls]
    for _ in range(50):
        if len(started) >= 2:
            break
        await asyncio.sleep(0.01)
    # Different audiences run side by side (no global lock); the duplicate
    # "overview" request joined the in-flight computation.
    assert sorted(started) == ["overview", "real"]
    release.set()
    results = await asyncio.gather(*tasks)
    assert [r["audience"] for r in results] == ["overview", "overview", "real"]
    admin_usage.clear_usage_caches()


# ---------------------------------------------------------------------------
# Postgres parity (CI)
# ---------------------------------------------------------------------------


async def test_embedding_token_estimate_in_sql_matches_python(
    client, db_session
) -> None:
    session = await session_at(db_session, datetime(2026, 3, 1), unique_ip())
    for turn, question in enumerate(
        ["", " ", "abc", "abcd", "x" * 4001, "héllo wörld"]
    ):
        await exchange(
            db_session, session, turn, datetime(2026, 3, 1, 1), question=question
        )
    await db_session.flush()

    res = await client.get("/admin/usage/embedding")
    assert res.status_code == 200
    contents = (
        (
            await db_session.execute(
                select(ChatMessageRecord.content).where(
                    ChatMessageRecord.role == "user"
                )
            )
        )
        .scalars()
        .all()
    )
    chunk_chars = sum(
        len(c or "")
        for c in (await db_session.execute(select(Chunk.content))).scalars()
    )
    expected = chunk_chars // 4 + sum(admin_routes._est_tokens(c) for c in contents)
    assert res.json()["input_tokens"] == expected
    assert res.json()["requests"] >= len(contents)


async def test_usage_llm_is_cached_until_cleared(
    client, db_session, monkeypatch
) -> None:
    admin_usage.clear_usage_caches()
    runs = 0
    real = admin_routes._usage_llm_payload

    async def counting(db):
        nonlocal runs
        runs += 1
        return await real(db)

    monkeypatch.setattr(admin_routes, "_usage_llm_payload", counting)
    first = await client.get("/admin/usage/llm")
    second = await client.get("/admin/usage/llm")
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert runs == 1
    admin_usage.clear_usage_caches()
    await client.get("/admin/usage/llm")
    assert runs == 2
    admin_usage.clear_usage_caches()


async def test_latency_stats_aggregate_in_sql(client, db_session) -> None:
    now = datetime.utcnow()
    before = await admin_routes._latency_stats(db_session)
    for llm, rerank in ((100.0, 10.0), (300.0, None)):
        db_session.add(
            LatencyRecord(
                id=uuid.uuid4(),
                request_id=f"perf-{uuid.uuid4().hex[:12]}",
                created_at=now - timedelta(minutes=1),
                total_ms=llm + 50,
                llm_ms=llm,
                reranker_ms=rerank,
            )
        )
    await db_session.flush()
    after = await admin_routes._latency_stats(db_session)
    assert after["requests"] == before["requests"] + 2
    assert after["reranker_requests"] == before["reranker_requests"] + 1
    assert isinstance(after["llm_ms"], float)


async def test_unfiltered_trace_total_matches_latency_rows(client, db_session) -> None:
    res = await client.get("/admin/governance/traces/page?limit=1")
    assert res.status_code == 200
    rows = (
        await db_session.execute(text("SELECT count(*) FROM latency_metrics"))
    ).scalar_one()
    body = res.json()
    if not body["total_is_estimate"]:
        assert body["total"] == rows


async def test_user_turn_newest_first_index_exists(db_session) -> None:
    found = (
        await db_session.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE indexname = 'ix_chat_messages_user_created_desc'"
            )
        )
    ).scalar_one_or_none()
    assert found is not None
    assert "DESC NULLS LAST" in found
    assert "WHERE" in found and "'user'" in found
