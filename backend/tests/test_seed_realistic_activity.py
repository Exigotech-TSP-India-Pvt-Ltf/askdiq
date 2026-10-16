"""Unit tests for the pure generators in scripts/seed_realistic_activity.py (no DB)."""

from __future__ import annotations

import ipaddress
import random
import re
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

import pytest

from app.governance.costing import compute_exchange_cost
from app.insights.taxonomy import DEFAULT_TAXONOMY
from scripts import seed_realistic_activity as seed
from scripts.seed_realistic_corpus import TEST_ACTORS

NOW = datetime(2026, 9, 30, 6)
KB = [
    seed.KbChunk(
        chunk_id=f"00000000-0000-4000-8000-{i:012d}",
        document_id=f"10000000-0000-4000-8000-{i % 4:012d}",
        source_name=("e8-learn-more.md", "zero-trust-learn-more.md", "trust-center.md", "about-us.md")[i % 4],
        heading="Overview",
        content=(
            "## Overview\nThe Essential Eight is a prioritised set of mitigation strategies "
            "published by the ACSC. DeployIQ assesses Microsoft 365 tenants against each "
            "maturity level and produces evidence for auditors. Zero Trust assumes breach "
            "and verifies every identity and device explicitly before granting access."
        ),
    )
    for i in range(12)
]  # fmt: skip


def _collect(
    cfg: seed.SeedConfig, kb: list[seed.KbChunk] = KB
) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {t: [] for t in seed.TABLES}
    for bundle in seed.generate(cfg, kb, DEFAULT_TAXONOMY):
        for table, rows in bundle.rows.items():
            out[table].extend(rows)
    return out


@pytest.fixture(scope="module")
def rows() -> dict[str, list[dict[str, Any]]]:
    return _collect(seed.SeedConfig(seed=7, scale=0.1, days=60, eval_rate=0.2, now=NOW))


def _assistants(rows: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return [m for m in rows["chat_messages"] if m["role"] == "assistant"]


# --- determinism ---------------------------------------------------------


def test_same_seed_is_deterministic() -> None:
    cfg = seed.SeedConfig(seed=11, scale=0.01, days=20, now=NOW)
    assert _collect(cfg) == _collect(
        seed.SeedConfig(seed=11, scale=0.01, days=20, now=NOW)
    )


def test_different_seed_differs() -> None:
    a = _collect(seed.SeedConfig(seed=1, scale=0.01, days=20, now=NOW))
    b = _collect(seed.SeedConfig(seed=2, scale=0.01, days=20, now=NOW))
    assert {r["id"] for r in a["chat_sessions"]}.isdisjoint(
        {r["id"] for r in b["chat_sessions"]}
    )


# --- volume and distributions --------------------------------------------


def test_scale_controls_volume(rows) -> None:
    assert len(rows["chat_sessions"]) == 520
    assert len(_assistants(rows)) >= len(rows["chat_sessions"])
    assert len(rows["chat_messages"]) == 2 * len(_assistants(rows))
    assert len(rows["latency_metrics"]) == len(_assistants(rows))
    assert len(rows["turn_insights"]) == len(_assistants(rows))


def test_full_scale_targets_ten_thousand_exchanges() -> None:
    cfg = seed.SeedConfig()
    assert cfg.sessions >= 5000
    assert 1500 <= cfg.visitors <= 3000


def test_outcome_mix_is_realistic(rows) -> None:
    outcomes = Counter(m["outcome"] for m in _assistants(rows))
    total = sum(outcomes.values())
    assert 0.55 < outcomes["answered"] / total < 0.9
    for outcome in ("ungrounded", "blocked", "conversational", "llm_failed"):
        assert outcomes[outcome] > 0, outcome


def test_multi_turn_sessions_exist(rows) -> None:
    turns = Counter(m["session_id"] for m in _assistants(rows))
    assert max(turns.values()) >= 3
    assert sum(1 for n in turns.values() if n > 1) > len(turns) * 0.3


def test_feedback_rate_and_rating_skew(rows) -> None:
    fb = rows["message_feedback"]
    rate = len(fb) / len(_assistants(rows))
    assert 0.05 < rate < 0.2
    ratings = Counter(f["rating"] for f in fb)
    assert set(ratings) <= {1, 2, 3, 4, 5}
    assert ratings[5] + ratings[4] > ratings[1] + ratings[2]
    assert any(f["comment"] for f in fb)
    assert {f["triage_status"] for f in fb} <= {"new", "reviewed", "dismissed"}


def test_eval_rate_is_respected(rows) -> None:
    ratio = len(rows["evaluation_records"]) / len(_assistants(rows))
    assert 0.12 < ratio < 0.28
    rec = rows["evaluation_records"][0]
    assert rec["latency_ms"] is not None
    assert rec["citation_precision"] is not None


def test_timestamps_follow_window_trend_and_weekdays(rows) -> None:
    created = [s["created_at"] for s in rows["chat_sessions"]]
    assert min(created) >= NOW - timedelta(days=61)
    assert max(m["created_at"] for m in rows["chat_messages"]) < NOW
    half = NOW - timedelta(days=30)
    assert sum(c >= half for c in created) > sum(c < half for c in created)
    weekday = sum(c.weekday() < 5 for c in created) / 5
    weekend = sum(c.weekday() >= 5 for c in created) / 2
    assert weekday > 2 * weekend


# --- consistency with the live writers ------------------------------------


def test_costs_use_the_real_costing_function(rows) -> None:
    for m in _assistants(rows)[:200]:
        td = m["token_details"]["tokens"]
        expected, _ = compute_exchange_cost(
            prompt_tokens=td["prompt"],
            completion_tokens=td["completion"],
            reasoning_tokens=td["reasoning"],
            cached_tokens=td["cached"],
            query_embed_tokens=td["query_embed"],
            guardrail_embed_tokens=td["guardrail_embed"],
        )
        assert m["cost_usd"] == expected


def test_token_columns_match_pipeline_stage(rows) -> None:
    for m in _assistants(rows):
        if m["outcome"] in ("conversational", "blocked", "llm_failed"):
            assert m["prompt_tokens"] is None
        else:
            assert m["prompt_tokens"] > 0
            assert m["total_tokens"] == m["prompt_tokens"] + m["completion_tokens"]


def test_ip_only_on_user_rows_and_every_turn_has_query_audit(rows) -> None:
    for m in rows["chat_messages"]:
        assert (m.get("ip_address") is not None) == (m["role"] == "user")
    audited = {a["request_id"] for a in rows["audit_log"] if a["action"] == "query"}
    assert {lat["request_id"] for lat in rows["latency_metrics"]} == audited


def test_conversational_and_blocked_latency_have_no_stages(rows) -> None:
    by_req = {m["request_id"]: m for m in _assistants(rows)}
    for lat in rows["latency_metrics"]:
        outcome = by_req[lat["request_id"]]["outcome"]
        if outcome == "conversational":
            assert lat["llm_ms"] is None and lat["retrieval_ms"] is None
        if outcome == "answered":
            assert lat["retrieval_ms"] >= lat["embedding_ms"] + lat["reranker_ms"]
            assert lat["total_ms"] > lat["llm_ms"]


def test_answers_cite_real_kb_chunks(rows) -> None:
    kb_ids = {c.chunk_id for c in KB}
    queries = [a for a in rows["audit_log"] if a["action"] == "query"]
    cited = [s["chunk_id"] for a in queries for s in a["detail"]["sources"]]
    assert cited and set(cited) <= kb_ids


def test_empty_kb_falls_back_gracefully() -> None:
    out = _collect(seed.SeedConfig(seed=3, scale=0.01, days=10, now=NOW), kb=[])
    assert all(m["content"] for m in out["chat_messages"])


def test_insight_labels_use_valid_codes(rows) -> None:
    for label in rows["turn_insights"]:
        assert label["intent"] in DEFAULT_TAXONOMY.intents
        assert label["bot_quality"] in DEFAULT_TAXONOMY.qualities
        assert label["issue"] is None or label["issue"] in DEFAULT_TAXONOMY.issues
        assert label["classified_by"] in ("rule", "llm")
    assert {lbl["classified_by"] for lbl in rows["turn_insights"]} == {"rule", "llm"}


def test_session_title_is_first_question(rows) -> None:
    firsts = {
        m["session_id"]: m
        for m in rows["chat_messages"]
        if m["role"] == "user" and m["turn_index"] == 0
    }
    for s in rows["chat_sessions"][:100]:
        assert s["title"] == firsts[s["id"]]["content"][:80]


# --- tagging and privacy ---------------------------------------------------


def test_every_row_is_tagged_for_purge(rows) -> None:
    for table in ("latency_metrics", "evaluation_records", "audit_log"):
        assert all(r["request_id"].startswith(seed.SEED_PREFIX) for r in rows[table])
    for m in _assistants(rows):
        assert m["request_id"].startswith(seed.SEED_PREFIX)
        assert m["token_details"]["seed"] is True
    llm_labels = [r for r in rows["turn_insights"] if r["classified_by"] == "llm"]
    assert all(r["model"] == seed.SEED_MODEL for r in llm_labels)


def test_no_real_pii(rows) -> None:
    emails = {s["user_email"] for s in rows["chat_sessions"]}
    assert all(e.endswith(seed.EMAIL_TLD) for e in emails)
    test_ips = {"10.20.0.0/16", "192.168.50.0/24"}
    nets = [ipaddress.ip_network(n) for n in test_ips]
    for s in rows["chat_sessions"]:
        ip = ipaddress.ip_address(s["created_ip"])
        if s["user_email"] in TEST_ACTORS:
            assert any(ip in n for n in nets)
        else:
            assert ip.is_global, s["created_ip"]
    phone = re.compile(r"04\d{2} \d{3} \d{3}")
    for a in rows["audit_log"]:
        if a["action"] == "query":
            assert not phone.search(a["detail"]["query"])
            assert "@" not in a["detail"]["query"]


def test_test_traffic_share(rows) -> None:
    test = sum(s["user_email"] in TEST_ACTORS for s in rows["chat_sessions"])
    assert 0 < test < len(rows["chat_sessions"]) * 0.1


def test_visitors_are_distinct_with_heavy_tail() -> None:
    visitors = seed.make_visitors(random.Random(5), 300)  # noqa: S311 - test data
    real = [v for v in visitors if not v.is_test]
    assert len({v.email for v in real}) == 300
    assert max(v.activity for v in real) > 5 * min(v.activity for v in real)


# --- CLI -------------------------------------------------------------------


def test_cli_defaults_to_dry_run() -> None:
    args = seed.parse_args([])
    assert args.apply is False and args.purge is False and args.seed == 42


@pytest.mark.parametrize(
    "argv", [["--scale", "0"], ["--days", "0"], ["--eval-rate", "2"]]
)
def test_cli_rejects_out_of_range(argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        seed.parse_args(argv)


def test_rollup_days_cover_window() -> None:
    cfg = seed.SeedConfig(days=10, now=NOW)
    days = seed.rollup_days(cfg)
    assert days == sorted(days)
    assert len(days) >= 11
