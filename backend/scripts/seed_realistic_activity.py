"""Seed realistic end-user chat activity so every admin dashboard has full data.

Generates visitors, multi-turn chat sessions, assistant answers built from the
real knowledge base (citations reference real chunk ids), token usage and cost
(``app.governance.costing``), per-stage latency, audit rows, user feedback,
online-evaluation records and insights labels, then recomputes the insights
daily rollup with the real rollup job (``InsightsService.rollup``).

Every seeded row is tagged so it can be found and purged:

- ``chat_messages.request_id`` / ``latency_metrics.request_id`` /
  ``evaluation_records.request_id`` / ``audit_log.request_id`` start with
  ``seed-``; ``chat_messages.token_details`` carries ``"seed": true``.
- Visitor emails use the reserved ``.example`` TLD; seeded test-IP rules have a
  ``seed:`` description; seeded insights labels use model ``seed-synthetic``.

``--purge`` deletes seeded sessions (cascading to messages, feedback,
evaluation records and insights labels), latency rows and test-IP rules, then
recomputes the rollup. ``audit_log`` is append-only (trigger
``trg_audit_log_immutable``), so seeded audit rows are intentionally kept; they
remain identifiable by their ``seed-`` request id.

Dry-run by default. Usage (from ``backend/``)::

    venv\\Scripts\\python.exe scripts/seed_realistic_activity.py            # dry run
    venv\\Scripts\\python.exe scripts/seed_realistic_activity.py --apply
    venv\\Scripts\\python.exe scripts/seed_realistic_activity.py --scale 0.1 --seed 7 --apply
    venv\\Scripts\\python.exe scripts/seed_realistic_activity.py --purge --apply
"""

from __future__ import annotations

import argparse
import asyncio
import bisect
import functools
import ipaddress
import logging
import math
import random
import re
import sys
import time
import uuid
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import Table, delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.governance.costing import compute_exchange_cost
from app.governance.online_eval import compute_attribution_metrics
from app.governance.pii import redact_pii
from app.guardrails.conversational import detect_conversational_intent
from app.guardrails.pre_checks import run_pre_checks, sanitize_query
from app.harness.orchestrator import _fallback_response
from app.insights.rules import classify_by_rule, map_outcome
from app.insights.taxonomy import DEFAULT_TAXONOMY, BotQuality, Taxonomy
from app.insights.types import TurnInput
from app.models.db_models import (
    AuditLog,
    ChatMessageRecord,
    ChatSession,
    Chunk,
    Document,
    EvaluationRecord,
    LatencyRecord,
    MessageFeedback,
    TestIpConfig,
    TurnInsight,
)
from scripts.seed_realistic_corpus import (
    ANSWER_CLOSERS,
    ANSWER_OPENERS,
    CAPABILITY,
    FAREWELLS,
    FEEDBACK_NEGATIVE,
    FEEDBACK_POSITIVE,
    FIRST_NAMES,
    GIBBERISH_QUERIES,
    GREETINGS,
    INJECTION_QUERIES,
    IPV4_PREFIXES,
    IPV6_PREFIXES,
    JUDGE_REASONS_FAIL,
    JUDGE_REASONS_PASS,
    LAST_NAMES,
    OFF_TOPIC_QUERIES,
    ORG_DOMAINS,
    PII_QUERIES,
    TEST_ACTORS,
    TEST_IP_ENTRIES,
    THANKS,
    TOPICS,
    Topic,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("seed_realistic")

SEED_PREFIX = "seed-"
SEED_MODEL = "seed-synthetic"
SEED_TEST_IP_PREFIX = "seed:"
EMAIL_TLD = ".example"

BASE_VISITORS = 2000
BASE_SESSIONS = 5200
DEFAULT_DAYS = 120
BATCH_ROWS = 1000
MAX_KB_CHUNKS = 5000
TEST_TRAFFIC_SHARE = 0.04
FEEDBACK_RATE = 0.11
AEST_OFFSET_HOURS = 10
_REVIEWERS = ("analyst@deployiq-ops.example", "grc.lead@deployiq-ops.example")

# Share of substantive user turns by scenario (the rest are topic questions).
SCENARIO_WEIGHTS: dict[str, float] = {
    "topic": 0.80,
    "off_topic": 0.06,
    "injection": 0.015,
    "gibberish": 0.025,
    "pii": 0.02,
    "capability": 0.02,
}
# Outcome mix for topic questions that pass the pre-guardrails.
TOPIC_OUTCOMES: dict[str, float] = {
    "answered": 0.86,
    "ungrounded": 0.08,
    "low_retrieval": 0.05,
    "llm_failed": 0.01,
}
# Local-hour weights (AEST) for when people chat.
HOUR_WEIGHTS: tuple[float, ...] = (
    0.15, 0.08, 0.05, 0.04, 0.05, 0.12, 0.35, 0.7, 1.3, 1.9, 2.1, 2.0,
    1.6, 1.8, 2.0, 1.9, 1.6, 1.1, 0.7, 0.55, 0.6, 0.55, 0.4, 0.25,
)  # fmt: skip
WEEKDAY_WEIGHTS: tuple[float, ...] = (1.05, 1.15, 1.15, 1.1, 0.9, 0.3, 0.25)

# Tables written, in dependency order.
TABLES: tuple[str, ...] = (
    "test_ip_configs",
    "chat_sessions",
    "chat_messages",
    "latency_metrics",
    "audit_log",
    "message_feedback",
    "evaluation_records",
    "turn_insights",
)
_MODELS: dict[str, Any] = {
    "test_ip_configs": TestIpConfig,
    "chat_sessions": ChatSession,
    "chat_messages": ChatMessageRecord,
    "latency_metrics": LatencyRecord,
    "audit_log": AuditLog,
    "message_feedback": MessageFeedback,
    "evaluation_records": EvaluationRecord,
    "turn_insights": TurnInsight,
}


# --------------------------------------------------------------------------
# Value objects
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class KbChunk:
    chunk_id: str
    document_id: str
    source_name: str
    heading: str
    content: str


@dataclass(frozen=True, slots=True)
class Visitor:
    email: str
    name: str
    org: str
    ips: tuple[str, ...]
    activity: float
    is_test: bool = False


@dataclass(slots=True)
class SeedConfig:
    seed: int = 42
    scale: float = 1.0
    days: int = DEFAULT_DAYS
    eval_rate: float = 0.10
    now: datetime = field(
        default_factory=lambda: datetime.now(UTC).replace(
            tzinfo=None, minute=0, second=0, microsecond=0
        )
    )

    @property
    def visitors(self) -> int:
        return max(5, round(BASE_VISITORS * self.scale))

    @property
    def sessions(self) -> int:
        return max(10, round(BASE_SESSIONS * self.scale))


@dataclass(slots=True)
class Bundle:
    """All rows generated for one chat session, keyed by table name."""

    rows: dict[str, list[dict[str, Any]]] = field(
        default_factory=lambda: {t: [] for t in TABLES}
    )

    def add(self, table: str, row: dict[str, Any]) -> None:
        self.rows[table].append(row)


# --------------------------------------------------------------------------
# Pure generators (deterministic for a given ``random.Random``)
# --------------------------------------------------------------------------


def new_uuid(rng: random.Random) -> uuid.UUID:
    return uuid.UUID(int=rng.getrandbits(128), version=4)


def request_id(rng: random.Random, kind: str = "q") -> str:
    return f"{SEED_PREFIX}{kind}-{new_uuid(rng).hex[:24]}"


def _weighted(rng: random.Random, weights: dict[str, float]) -> str:
    keys = list(weights)
    return rng.choices(keys, weights=[weights[k] for k in keys])[0]


def public_ipv4(rng: random.Random) -> str:
    prefixes = [p for p, _ in IPV4_PREFIXES]
    weights = [w for _, w in IPV4_PREFIXES]
    prefix = rng.choices(prefixes, weights=weights)[0]
    return f"{prefix}.{rng.randint(1, 254)}.{rng.randint(1, 254)}"


def public_ipv6(rng: random.Random) -> str:
    prefix = rng.choice(IPV6_PREFIXES)
    groups = ":".join(f"{rng.getrandbits(16):x}" for _ in range(3))
    return f"{prefix}:{rng.getrandbits(16):x}::{groups}"


def test_ip(rng: random.Random) -> str:
    if rng.random() < 0.6:  # noqa: PLR2004 - QA subnet share
        return f"10.20.{rng.randint(0, 255)}.{rng.randint(1, 254)}"
    return f"192.168.50.{rng.randint(2, 254)}"


def make_visitors(rng: random.Random, count: int) -> list[Visitor]:
    """Distinct visitors with stable emails, 1-3 IPs and a heavy-tailed activity."""
    visitors: list[Visitor] = []
    seen: set[str] = set()
    corporate_nat: dict[str, str] = {}
    while len(visitors) < count:
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        org = rng.choice(ORG_DOMAINS)
        local = re.sub(r"[^a-z.]", "", f"{first[0]}.{last}".lower())
        email = f"{local}@{org}"
        if email in seen:
            email = f"{local}{rng.randint(2, 99)}@{org}"
            if email in seen:
                continue
        seen.add(email)
        ips: list[str] = []
        # Colleagues at one org often share a corporate NAT egress address.
        if rng.random() < 0.35:  # noqa: PLR2004
            ips.append(corporate_nat.setdefault(org, public_ipv4(rng)))
        for _ in range(rng.choices((1, 2, 3), weights=(0.6, 0.3, 0.1))[0]):
            ips.append(public_ipv6(rng) if rng.random() < 0.12 else public_ipv4(rng))  # noqa: PLR2004
        visitors.append(
            Visitor(
                email=email,
                name=f"{first} {last}",
                org=org.removesuffix(EMAIL_TLD),
                ips=tuple(dict.fromkeys(ips)),
                activity=rng.paretovariate(1.6),
            )
        )
    for actor in TEST_ACTORS:
        visitors.append(
            Visitor(
                email=actor,
                name="QA",
                org="qa",
                ips=tuple(test_ip(rng) for _ in range(3)),
                activity=0.0,
                is_test=True,
            )
        )
    return visitors


def day_weights(cfg: SeedConfig) -> list[tuple[date, float]]:
    """Per-day weight: linear growth trend times a weekday factor."""
    start = (cfg.now - timedelta(days=cfg.days)).date()
    out: list[tuple[date, float]] = []
    for offset in range(cfg.days + 1):
        day = start + timedelta(days=offset)
        trend = 0.45 + 1.1 * offset / max(1, cfg.days)
        out.append((day, trend * WEEKDAY_WEIGHTS[day.weekday()]))
    return out


def session_start_times(
    rng: random.Random, cfg: SeedConfig, count: int
) -> list[datetime]:
    """``count`` UTC start times following the trend + diurnal (AEST) pattern."""
    days = day_weights(cfg)
    day_values = [d for d, _ in days]
    cumulative: list[float] = []
    total = 0.0
    for _, w in days:
        total += w
        cumulative.append(total)
    starts: list[datetime] = []
    while len(starts) < count:
        idx = bisect.bisect_left(cumulative, rng.random() * total)
        day = day_values[min(idx, len(day_values) - 1)]
        local_hour = rng.choices(range(24), weights=HOUR_WEIGHTS)[0]
        local = datetime(day.year, day.month, day.day, local_hour) + timedelta(
            seconds=rng.randint(0, 3599)
        )
        utc = local - timedelta(hours=AEST_OFFSET_HOURS)
        if utc < cfg.now - timedelta(minutes=10):
            starts.append(utc)
    starts.sort()
    return starts


def typo(rng: random.Random, text_in: str) -> str:
    """Introduce a realistic slip (swap/drop a letter, lowercase, no '?')."""
    words = text_in.split()
    candidates = [i for i, w in enumerate(words) if len(w) > 4]  # noqa: PLR2004
    if candidates:
        i = rng.choice(candidates)
        w = words[i]
        j = rng.randint(1, len(w) - 2)
        words[i] = (
            w[:j] + w[j + 1] + w[j] + w[j + 2 :]
            if rng.random() < 0.5
            else w[:j] + w[j + 1 :]
        )  # noqa: PLR2004
    out = " ".join(words)
    if rng.random() < 0.5:  # noqa: PLR2004
        out = out.lower().rstrip("?")
    return out


def paraphrase(rng: random.Random, question: str) -> str:
    roll = rng.random()
    if roll < 0.12:  # noqa: PLR2004
        return typo(rng, question)
    if roll < 0.20:  # noqa: PLR2004
        return f"Quick question - {question[0].lower()}{question[1:]}"
    if roll < 0.26:  # noqa: PLR2004
        return f"Hi, {question[0].lower()}{question[1:]}"
    if roll < 0.30:  # noqa: PLR2004
        return question.rstrip("?") + " for a government agency?"
    return question


_MD_NOISE = re.compile(r"(\[([^\]]+)\]\([^)]+\))|[#*_>`|]+")


@functools.lru_cache(maxsize=8192)
def sentences_from(content: str) -> tuple[str, ...]:
    cleaned = _MD_NOISE.sub(lambda m: m.group(2) or " ", content)
    parts = re.split(r"(?<=[.!?])\s+|\n+", cleaned)
    return tuple(p.strip(" -") for p in parts if 40 <= len(p.strip()) <= 320)  # noqa: PLR2004


def pick_chunks(
    rng: random.Random, kb: Sequence[KbChunk], topic: Topic | None, k: int
) -> list[KbChunk]:
    if not kb:
        return []
    pool = _topic_pool(tuple(kb), topic) if topic is not None else kb
    return rng.sample(pool, min(k, len(pool)))


@functools.lru_cache(maxsize=64)
def _topic_pool(kb: tuple[KbChunk, ...], topic: Topic) -> tuple[KbChunk, ...]:
    matching = tuple(
        c for c in kb if any(kw in c.source_name.lower() for kw in topic.keywords)
    )
    return matching or kb


def build_answer(rng: random.Random, topic: Topic, chunks: Sequence[KbChunk]) -> str:
    sentences: list[str] = []
    for chunk in chunks:
        found = sentences_from(chunk.content)
        if found:
            sentences.extend(rng.sample(found, min(len(found), rng.randint(1, 2))))
    if not sentences:
        sentences = [
            f"DeployIQ provides guidance on {topic.key.replace('_', ' ')} as part "
            "of its Microsoft 365 security platform."
        ]
    opener, closer = rng.choice(ANSWER_OPENERS), rng.choice(ANSWER_CLOSERS)
    if len(sentences) > 2 and rng.random() < 0.5:  # noqa: PLR2004
        body = "\n".join(f"- {s}" for s in sentences[:5])
    else:
        body = " ".join(sentences[:4])
    return "\n\n".join(p for p in (opener, body, closer) if p)


def synthetic_pii_query(rng: random.Random) -> str:
    template = rng.choice(PII_QUERIES)
    first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
    return template.format(
        email=f"{first.lower()}.{last.lower()}@{rng.choice(ORG_DOMAINS)}",
        phone=f"04{rng.randint(10, 99)} {rng.randint(100, 999)} {rng.randint(100, 999)}",
        name=f"{first} {last}",
        org=rng.choice(ORG_DOMAINS).removesuffix(EMAIL_TLD),
    )


@dataclass(slots=True)
class Turn:
    """One generated exchange before it is turned into rows."""

    query: str
    answer: str
    outcome: str
    grounded: bool
    flags: list[str]
    chunks: list[KbChunk]
    scores: list[float]
    topic: Topic | None
    stage: str  # conversational | pre_block | retrieval_block | llm_failed | generated
    confidence: float = 0.0


def _fallback(flags: list[str], outcome: str) -> str:
    return _fallback_response(
        request_id="seed", flags=flags, latency_ms=0.0, outcome=outcome
    ).answer


@functools.lru_cache(maxsize=16384)
def guardrail_route(query: str) -> tuple[str | None, list[str]]:
    """(canned small-talk reply, pre-guardrail flags) from the real guardrails."""
    canned = detect_conversational_intent(query)
    if canned is not None:
        return canned, []
    flags = run_pre_checks(query, get_settings().max_query_length)
    if not flags:
        _, flags = sanitize_query(query)
    return None, flags


def plan_turn(
    rng: random.Random,
    kb: Sequence[KbChunk],
    query: str,
    topic: Topic | None,
    forced: str | None = None,
) -> Turn:
    """Route ``query`` through the real guardrail functions, then pick an outcome."""
    canned, pre_flags = guardrail_route(query)
    if canned is not None:
        return Turn(
            query,
            canned,
            "conversational",
            True,
            [],
            [],
            [],
            None,
            "conversational",
            1.0,
        )
    if pre_flags:
        return Turn(
            query,
            _fallback(pre_flags, "blocked"),
            "blocked",
            False,
            list(pre_flags),
            [],
            [],
            topic,
            "pre_block",
        )

    path = forced or _weighted(rng, TOPIC_OUTCOMES)
    if topic is None or path == "low_retrieval":
        chunks = pick_chunks(rng, kb, None, rng.randint(2, 5))
        scores = sorted(
            (round(rng.uniform(0.02, 0.14), 4) for _ in chunks), reverse=True
        )
        flags = ["off_topic"]
        return Turn(
            query,
            _fallback(flags, "blocked"),
            "blocked",
            False,
            flags,
            chunks,
            scores,
            topic,
            "retrieval_block",
        )

    chunks = pick_chunks(rng, kb, topic, rng.randint(3, 5))
    scores = sorted((round(rng.uniform(0.32, 0.86), 4) for _ in chunks), reverse=True)
    if path == "llm_failed":
        flags = ["llm_call_failed"]
        return Turn(
            query,
            _fallback(flags, "llm_failed"),
            "llm_failed",
            False,
            flags,
            chunks,
            scores,
            topic,
            "llm_failed",
        )
    if path == "ungrounded":
        flags = [rng.choice(("groundedness_check_failed", "unsupported_numeric_claim"))]
        return Turn(
            query,
            _fallback(flags, "ungrounded"),
            "ungrounded",
            False,
            flags,
            chunks,
            scores,
            topic,
            "generated",
            round(rng.uniform(0.1, 0.44), 4),
        )
    answer = build_answer(rng, topic, chunks)
    return Turn(
        query,
        answer,
        "answered",
        True,
        [],
        chunks,
        scores,
        topic,
        "generated",
        round(rng.uniform(0.46, 0.97), 4),
    )


def plan_session(
    rng: random.Random, kb: Sequence[KbChunk], visitor: Visitor
) -> list[Turn]:
    """A realistic multi-turn conversation (greeting, questions, follow-ups, thanks)."""
    turns: list[Turn] = []
    if visitor.is_test:
        topic = rng.choice(TOPICS)
        return [plan_turn(rng, kb, rng.choice(topic.questions), topic)]
    if rng.random() < 0.12:  # noqa: PLR2004
        turns.append(plan_turn(rng, kb, rng.choice(GREETINGS), None))
    scenario = _weighted(rng, SCENARIO_WEIGHTS)
    if scenario == "off_topic":
        turns.append(plan_turn(rng, kb, rng.choice(OFF_TOPIC_QUERIES), None))
    elif scenario == "injection":
        turns.append(plan_turn(rng, kb, rng.choice(INJECTION_QUERIES), None))
    elif scenario == "gibberish":
        turns.append(plan_turn(rng, kb, rng.choice(GIBBERISH_QUERIES), None))
    elif scenario == "capability":
        turns.append(plan_turn(rng, kb, rng.choice(CAPABILITY), None))
    topic = rng.choices(TOPICS, weights=[t.weight for t in TOPICS])[0]
    if scenario == "pii":
        turns.append(plan_turn(rng, kb, synthetic_pii_query(rng), topic, "answered"))
    else:
        turns.append(
            plan_turn(rng, kb, paraphrase(rng, rng.choice(topic.questions)), topic)
        )
    extra = rng.choices((0, 1, 2, 3, 4), weights=(0.42, 0.3, 0.16, 0.08, 0.04))[0]
    for _ in range(extra):
        if rng.random() < 0.65:  # noqa: PLR2004
            question = rng.choice(topic.follow_ups)
        else:
            topic = rng.choices(TOPICS, weights=[t.weight for t in TOPICS])[0]
            question = paraphrase(rng, rng.choice(topic.questions))
        turns.append(plan_turn(rng, kb, question, topic))
    if rng.random() < 0.18:  # noqa: PLR2004
        turns.append(plan_turn(rng, kb, rng.choice(THANKS + FAREWELLS), None))
    return turns


@dataclass(frozen=True, slots=True)
class Usage:
    prompt: int | None
    completion: int | None
    reasoning: int | None
    cached: int
    query_embed: int
    guardrail_embed: int


def token_usage(rng: random.Random, turn: Turn, history_turns: int) -> Usage:
    """Token counts consistent with the pipeline stage the turn reached."""
    query_embed = max(2, len(turn.query) // 4)
    if turn.stage in ("conversational", "pre_block"):
        return Usage(None, None, None, 0, 0, 0)
    if turn.stage in ("retrieval_block", "llm_failed"):
        return Usage(None, None, None, 0, query_embed, 0)
    context = sum(len(c.content) for c in turn.chunks) // 4
    prompt = 900 + context + history_turns * rng.randint(60, 220) + rng.randint(0, 300)
    reasoning = rng.choice((0, 64, 128, 192, 256, 384, 512))
    completion = reasoning + max(40, len(turn.answer) // 4) + rng.randint(0, 40)
    cached = (prompt // 1024) * 1024 if rng.random() < 0.3 else 0  # noqa: PLR2004
    guardrail_embed = max(8, len(turn.answer) // 4)
    return Usage(prompt, completion, reasoning, cached, query_embed, guardrail_embed)


def stage_latency(
    rng: random.Random, turn: Turn, hour_load: float
) -> dict[str, float | None]:
    """Per-stage timings (ms) shaped like the live LatencyMiddleware output."""
    slow = 1.0 + 0.15 * hour_load

    def logn(median: float, sigma: float) -> float:
        return round(median * slow * math.exp(rng.gauss(0, sigma)), 2)

    stages: dict[str, float | None] = dict.fromkeys(
        (
            "embedding_ms",
            "retrieval_ms",
            "guardrail_pre_ms",
            "llm_ms",
            "guardrail_post_ms",
            "reranker_ms",
        )
    )
    if turn.stage in ("conversational", "pre_block"):
        return {**stages, "total_ms": round(rng.uniform(3, 25), 2)}
    stages["guardrail_pre_ms"] = round(rng.uniform(0.3, 4.0), 2)
    stages["embedding_ms"] = logn(160, 0.35)
    stages["reranker_ms"] = logn(260, 0.4)
    stages["retrieval_ms"] = round(
        stages["embedding_ms"] + stages["reranker_ms"] + logn(45, 0.5), 2
    )
    total = stages["guardrail_pre_ms"] + stages["retrieval_ms"]
    if turn.stage == "generated":
        stages["llm_ms"] = logn(3400, 0.45)
        stages["guardrail_post_ms"] = logn(170, 0.4)
        total += stages["llm_ms"] + stages["guardrail_post_ms"]
    elif turn.stage == "llm_failed":
        stages["llm_ms"] = logn(9000, 0.3)
        stages["guardrail_post_ms"] = 0.0
        total += stages["llm_ms"]
    return {**stages, "total_ms": round(total + rng.uniform(15, 70), 2)}


def feedback_for(
    rng: random.Random, turn: Turn
) -> tuple[int, str | None, list[str]] | None:
    """Rating/comment/tags, skewed by outcome; None when the user gave none."""
    if turn.stage == "conversational" or rng.random() > FEEDBACK_RATE * (
        1.6 if not turn.grounded else 1.0
    ):
        return None
    if turn.outcome == "answered":
        rating = rng.choices((5, 4, 3, 2, 1), weights=(0.52, 0.24, 0.1, 0.06, 0.08))[0]
    else:
        rating = rng.choices((5, 4, 3, 2, 1), weights=(0.05, 0.07, 0.15, 0.28, 0.45))[0]
    if rating >= 4:  # noqa: PLR2004
        comment = rng.choice(FEEDBACK_POSITIVE)
        return rating, comment or None, []
    comment, tags = rng.choice(FEEDBACK_NEGATIVE)
    if turn.outcome == "blocked" and "off_topic" not in tags and rng.random() < 0.5:  # noqa: PLR2004
        tags = (*tags, "off_topic")
    return rating, comment or None, list(tags)


def judge_scores(rng: random.Random, turn: Turn) -> tuple[float, float, float]:
    if turn.grounded and turn.outcome == "answered":
        faith = rng.choices((5.0, 4.0, 3.0, 2.0), weights=(0.6, 0.3, 0.07, 0.03))[0]
        return faith, rng.choice((4.0, 5.0, 5.0)), rng.choice((3.0, 4.0, 4.0, 5.0))
    if turn.outcome == "conversational":
        return 4.5, 4.5, 4.5
    return (
        rng.choice((1.0, 2.0, 2.0, 3.0)),
        rng.choice((2.0, 3.0, 3.5)),
        rng.choice((2.0, 3.0)),
    )


def insight_label(
    rng: random.Random,
    turn: TurnInput,
    topic: Topic | None,
    taxonomy: Taxonomy,
    *,
    abandoned: bool,
) -> dict[str, Any]:
    """Rule label when the real rule stage applies, else a synthetic 'LLM' label."""
    rule = classify_by_rule(turn, taxonomy)
    if rule is not None:
        return {
            "intent": rule.intent, "bot_quality": rule.bot_quality, "issue": rule.issue,
            "substantive": rule.substantive, "handled": rule.handled, "friction": None,
            "confidence": rule.confidence, "classified_by": "rule", "model": None,
        }  # fmt: skip
    flags = frozenset(turn.guardrail_flags)
    mapping = map_outcome(turn.outcome, turn.grounded, flags)
    intent = topic.intent if topic is not None else "OTHER"
    issue: str | None = None
    if mapping.bot_quality == BotQuality.FB:
        issue = "KB_GAP"
    elif mapping.bot_quality == BotQuality.PART:
        issue = rng.choice(("NARROW", "GENERIC", "KB_GAP", "WRONG"))
    elif intent == "CON" and rng.random() < 0.3:  # noqa: PLR2004
        issue = "PRICING"
    return {
        "intent": intent, "bot_quality": mapping.bot_quality.value, "issue": issue,
        "substantive": taxonomy.is_substantive(intent), "handled": mapping.handled,
        "friction": "abandoned" if abandoned and not mapping.handled else None,
        "confidence": round(rng.uniform(0.72, 0.98), 3), "classified_by": "llm",
        "model": SEED_MODEL,
    }  # fmt: skip


# --------------------------------------------------------------------------
# Row assembly
# --------------------------------------------------------------------------


def _sources(turn: Turn) -> list[dict[str, Any]]:
    return [
        {"chunk_id": c.chunk_id, "document_id": c.document_id, "score": s}
        for c, s in zip(turn.chunks, turn.scores, strict=True)
    ]


def session_rows(
    rng: random.Random,
    cfg: SeedConfig,
    kb: Sequence[KbChunk],
    visitor: Visitor,
    start: datetime,
    taxonomy: Taxonomy,
) -> Bundle:
    """Every row for one chat session (messages, latency, audit, feedback, ...)."""
    settings = get_settings()
    bundle = Bundle()
    session_id = new_uuid(rng)
    ip = rng.choice(visitor.ips)
    turns = plan_session(rng, kb, visitor)
    at = start
    title = "New chat"
    first_request = ""
    for index, turn in enumerate(turns):
        if index:
            at += timedelta(seconds=rng.randint(15, 240))
        if at >= cfg.now:
            break
        req = request_id(rng)
        first_request = first_request or req
        if title == "New chat":
            title = turn.query.strip()[:80] or title
        usage = token_usage(rng, turn, index)
        cost, details = compute_exchange_cost(
            prompt_tokens=usage.prompt or 0,
            completion_tokens=usage.completion or 0,
            reasoning_tokens=usage.reasoning or 0,
            cached_tokens=usage.cached,
            query_embed_tokens=usage.query_embed,
            guardrail_embed_tokens=usage.guardrail_embed,
        )
        llm_details: dict[str, Any] = {}
        if usage.prompt is not None:
            llm_details = {
                "llm_attempts": 1,
                "attempts": [{"attempt": 1, "usage": {
                    "prompt_tokens": usage.prompt, "completion_tokens": usage.completion,
                    "reasoning_tokens": usage.reasoning, "cached_tokens": usage.cached,
                    "total_tokens": (usage.prompt or 0) + (usage.completion or 0),
                    "visible_tokens": (usage.completion or 0) - (usage.reasoning or 0),
                }}],
                "cached_tokens": usage.cached,
            }  # fmt: skip
        token_details = {
            **details,
            **llm_details,
            "outcome": turn.outcome,
            "seed": True,
        }
        user_id, assistant_id = new_uuid(rng), new_uuid(rng)
        bundle.add("chat_messages", {
            "id": user_id, "session_id": session_id, "turn_index": index, "role": "user",
            "content": turn.query, "created_at": at, "ip_address": ip,
        })  # fmt: skip
        bundle.add("chat_messages", {
            "id": assistant_id, "session_id": session_id, "turn_index": index,
            "role": "assistant", "content": turn.answer, "created_at": at,
            "request_id": req, "grounded": turn.grounded, "guardrail_flags": turn.flags,
            "outcome": turn.outcome, "prompt_tokens": usage.prompt,
            "completion_tokens": usage.completion,
            "total_tokens": (usage.prompt + usage.completion) if usage.prompt is not None and usage.completion is not None else None,
            "reasoning_tokens": usage.reasoning, "embedding_tokens": usage.query_embed,
            "guardrail_tokens": usage.guardrail_embed, "cost_usd": cost,
            "token_details": token_details,
        })  # fmt: skip
        local_hour = (at + timedelta(hours=AEST_OFFSET_HOURS)).hour
        timings = stage_latency(rng, turn, HOUR_WEIGHTS[local_hour] / 2.1)
        bundle.add(
            "latency_metrics",
            {"id": new_uuid(rng), "request_id": req, "created_at": at, **timings},
        )
        if index == 0:
            bundle.add("audit_log", {
                "id": new_uuid(rng), "created_at": at, "request_id": req, "actor": visitor.email,
                "action": "session_created", "detail": {"session_id": str(session_id), "source": "query"},
                "ip_address": ip,
            })  # fmt: skip
        if turn.stage == "llm_failed":
            for attempt in (1, 2):
                bundle.add("audit_log", {
                    "id": new_uuid(rng), "created_at": at, "request_id": req, "actor": visitor.email,
                    "action": "llm_call_failed", "ip_address": ip,
                    "detail": {"attempt": attempt, "max_attempts": 2, "error_type": "APITimeoutError",
                               "status_code": None, "will_retry": attempt < 2},  # noqa: PLR2004
                })  # fmt: skip
        bundle.add("audit_log", {
            "id": new_uuid(rng), "created_at": at, "request_id": req, "actor": visitor.email,
            "action": "query", "ip_address": ip,
            "detail": {
                "query": redact_pii(turn.query)[0], "session_id": str(session_id),
                "grounded": turn.grounded, "guardrail_flags": turn.flags,
                "source_ids": [c.chunk_id for c in turn.chunks], "sources": _sources(turn),
                "cost_usd": cost, "outcome": turn.outcome, "tokens": details["tokens"],
            },
        })  # fmt: skip
        _feedback_rows(rng, bundle, turn, visitor, session_id, assistant_id, at, ip)
        if rng.random() < cfg.eval_rate:
            _eval_rows(
                rng,
                bundle,
                turn,
                req,
                session_id,
                assistant_id,
                at,
                ip,
                timings["total_ms"],
                visitor,
            )
        turn_input = TurnInput(
            assistant_id,
            session_id,
            turn.query,
            turn.answer,
            turn.outcome,
            turn.grounded,
            tuple(turn.flags),
        )
        label = insight_label(
            rng, turn_input, turn.topic, taxonomy, abandoned=index == len(turns) - 1
        )
        bundle.add("turn_insights", {
            "id": new_uuid(rng), "message_id": assistant_id, "session_id": session_id,
            "classified_at": (at + timedelta(minutes=rng.randint(1, 30))).replace(tzinfo=UTC), **label,
        })  # fmt: skip
    if not bundle.rows["chat_messages"]:
        return Bundle()
    last = max(r["created_at"] for r in bundle.rows["chat_messages"])
    bundle.add("chat_sessions", {
        "id": session_id, "user_email": visitor.email, "title": title,
        "created_at": start - timedelta(seconds=rng.randint(1, 20)), "last_activity_at": last,
        "expires_at": last + timedelta(minutes=settings.session_timeout_minutes), "created_ip": ip,
    })  # fmt: skip
    return bundle


def _feedback_rows(
    rng: random.Random, bundle: Bundle, turn: Turn, visitor: Visitor,
    session_id: uuid.UUID, assistant_id: uuid.UUID, at: datetime, ip: str,
) -> None:  # fmt: skip
    fb = feedback_for(rng, turn)
    if fb is None:
        return
    rating, comment, tags = fb
    created = at + timedelta(seconds=rng.randint(8, 600))
    triage, reviewed_by, reviewed_at = "new", None, None
    if rating <= 2 and rng.random() < 0.45:  # noqa: PLR2004
        triage = rng.choice(("reviewed", "reviewed", "dismissed"))
        reviewed_by = rng.choice(_REVIEWERS)
        reviewed_at = created + timedelta(hours=rng.randint(2, 96))
    bundle.add("message_feedback", {
        "id": new_uuid(rng), "message_id": assistant_id, "session_id": session_id,
        "actor": visitor.email, "rating": rating, "comment": comment, "created_at": created,
        "updated_at": reviewed_at or created, "reason_tags": tags, "triage_status": triage,
        "reviewed_by": reviewed_by, "reviewed_at": reviewed_at, "version": 1,
    })  # fmt: skip
    bundle.add("audit_log", {
        "id": new_uuid(rng), "created_at": created, "request_id": request_id(rng, "fb"),
        "actor": visitor.email, "action": "user_feedback", "ip_address": ip,
        "detail": {
            "rating": rating, "comment": redact_pii(comment)[0] if comment else None,
            "reason_tags": tags, "has_corrected_answer": False,
            "conversation_id": str(session_id), "message_id": str(assistant_id),
        },
    })  # fmt: skip


def _eval_rows(
    rng: random.Random, bundle: Bundle, turn: Turn, req: str, session_id: uuid.UUID,
    assistant_id: uuid.UUID, at: datetime, ip: str, latency_ms: float | None, visitor: Visitor,
) -> None:  # fmt: skip
    contexts = [c.content for c in turn.chunks]
    precision, coverage = compute_attribution_metrics(turn.answer, contexts)
    faith, relevance, completeness = judge_scores(rng, turn)
    flags = list(turn.flags)
    if faith <= 2:  # noqa: PLR2004
        flags.append("online_unfaithful")
    passed = turn.grounded is not False and faith >= 3  # noqa: PLR2004
    unsupported = (
        ["States a specific price not present in the sources."]
        if not passed and turn.topic is not None and turn.topic.intent == "CON"
        else []
    )
    bundle.add("evaluation_records", {
        "id": new_uuid(rng), "request_id": req, "message_id": assistant_id,
        "session_id": session_id, "audience": "testing" if visitor.is_test else "users",
        "query": turn.query, "answer": turn.answer, "passed": passed,
        "grounded": turn.grounded, "confidence": turn.confidence, "outcome": turn.outcome,
        "faithfulness": faith, "relevance": relevance, "completeness": completeness,
        "citation_precision": precision, "citation_coverage": coverage,
        "retrieval_recall": 1.0 if contexts and turn.grounded else 0.0,
        "safety_passed": True, "guardrail_flags": flags,
        "retrieved_chunks": [
            {"source_name": c.source_name, "section_heading": c.heading, "score": s,
             "chunk_id": c.chunk_id, "content": c.content[:500]}
            for c, s in zip(turn.chunks, turn.scores, strict=True)
        ],
        "judge_reason": rng.choice(JUDGE_REASONS_PASS if passed else JUDGE_REASONS_FAIL),
        "unsupported_claims": unsupported, "latency_ms": latency_ms,
        "created_at": at + timedelta(seconds=rng.randint(2, 40)),
    })  # fmt: skip
    if "online_unfaithful" in flags:
        bundle.add("audit_log", {
            "id": new_uuid(rng), "created_at": at + timedelta(seconds=45), "request_id": req,
            "actor": "system:online_eval", "action": "online_eval", "ip_address": ip,
            "detail": {"guardrail_flags": ["online_unfaithful"], "query_len": len(turn.query),
                       "answer_len": len(turn.answer),
                       "judge_scores": {"faithfulness": faith, "relevance": relevance, "completeness": completeness}},
        })  # fmt: skip


def generate(
    cfg: SeedConfig, kb: Sequence[KbChunk], taxonomy: Taxonomy = DEFAULT_TAXONOMY
) -> Iterator[Bundle]:
    """Yield one ``Bundle`` per session (bounded memory: nothing is accumulated)."""
    rng = random.Random(cfg.seed)
    visitors = make_visitors(rng, cfg.visitors)
    real = [v for v in visitors if not v.is_test]
    test = [v for v in visitors if v.is_test]
    weights = [v.activity for v in real]
    # Every visitor gets at least one session; the rest follow activity.
    n_test = max(1, round(cfg.sessions * TEST_TRAFFIC_SHARE))
    n_real = max(len(real), cfg.sessions - n_test)
    owners = list(real) + rng.choices(real, weights=weights, k=n_real - len(real))
    owners += [rng.choice(test) for _ in range(n_test)]
    rng.shuffle(owners)
    starts = session_start_times(rng, cfg, len(owners))
    for visitor, start in zip(owners, starts, strict=True):
        bundle = session_rows(rng, cfg, kb, visitor, start, taxonomy)
        if bundle.rows["chat_sessions"]:
            yield bundle


def test_ip_rows(rng: random.Random, now: datetime) -> list[dict[str, Any]]:
    return [
        {
            "id": new_uuid(rng),
            "ip_address": ip,
            "description": desc,
            "is_active": True,
            "created_at": now,
        }
        for ip, desc in TEST_IP_ENTRIES
    ]


# --------------------------------------------------------------------------
# Database I/O
# --------------------------------------------------------------------------


async def load_kb(engine: AsyncEngine) -> list[KbChunk]:
    stmt = (
        select(
            Chunk.id,
            Chunk.document_id,
            Chunk.content,
            Chunk.chunk_metadata,
            Document.source_name,
        )
        .join(Document, Document.id == Chunk.document_id)
        .order_by(Chunk.id)
        .limit(MAX_KB_CHUNKS)
    )
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).all()
    return [
        KbChunk(
            chunk_id=str(r.id),
            document_id=str(r.document_id),
            source_name=r.source_name,
            heading=str(
                (r.chunk_metadata or {}).get("section_heading")
                or (r.chunk_metadata or {}).get("section")
                or ""
            ),
            content=r.content,
        )
        for r in rows
    ]


async def load_taxonomy(engine: AsyncEngine) -> Taxonomy:
    from app.insights.repository import InsightsRepository

    async with async_sessionmaker(engine)() as session:
        return await InsightsRepository(session).load_taxonomy()


class Writer:
    """Buffers rows per table and flushes them as batched INSERT ... ON CONFLICT DO NOTHING."""

    def __init__(
        self, engine: AsyncEngine | None, batch_rows: int = BATCH_ROWS
    ) -> None:
        self.engine = engine
        self.batch_rows = batch_rows
        self.buffers: dict[str, list[dict[str, Any]]] = {t: [] for t in TABLES}
        self.counts: Counter[str] = Counter()

    def add(self, bundle: Bundle) -> None:
        for table, rows in bundle.rows.items():
            self.buffers[table].extend(rows)
            self.counts[table] += len(rows)

    def pending(self) -> int:
        return sum(len(rows) for rows in self.buffers.values())

    async def flush(self) -> None:
        if self.engine is None:
            for rows in self.buffers.values():
                rows.clear()
            return
        async with self.engine.begin() as conn:
            for table in TABLES:  # FK order
                rows = self.buffers[table]
                if not rows:
                    continue
                target: Table = _MODELS[table].__table__
                # One executemany per column set: omitted columns stay SQL NULL
                # (as with the ORM), never JSON 'null'.
                for group in group_by_columns(rows):
                    for start in range(0, len(group), self.batch_rows):
                        chunk = group[start : start + self.batch_rows]
                        await conn.execute(
                            pg_insert(target).on_conflict_do_nothing(), chunk
                        )
                rows.clear()


def group_by_columns(rows: Sequence[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(tuple(sorted(row)), []).append(row)
    return list(groups.values())


async def already_seeded(engine: AsyncEngine, session_id: uuid.UUID) -> bool:
    async with engine.connect() as conn:
        found = await conn.scalar(
            select(ChatSession.id).where(ChatSession.id == session_id)
        )
    return found is not None


def rollup_days(cfg: SeedConfig) -> list[date]:
    """Local days covering the window (UTC dates +/- 1 absorb any insights timezone)."""
    first = (cfg.now - timedelta(days=cfg.days)).date() - timedelta(days=1)
    last = cfg.now.date() + timedelta(days=1)
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


async def run_rollup(engine: AsyncEngine, days: list[date]) -> None:
    """Recompute insight_daily_rollup with the real rollup job (also clears the IP cache)."""
    from app.governance import audience
    from app.governance.test_ips import invalidate_test_ips_cache
    from app.insights.service import InsightsService

    invalidate_test_ips_cache()
    audience._cached_classification = {}  # noqa: SLF001 - fresh test-IP view for this process
    await InsightsService(engine=engine).rollup(days)


async def purge(engine: AsyncEngine, cfg: SeedConfig, *, apply: bool) -> dict[str, int]:
    seeded_sessions = (
        select(ChatMessageRecord.session_id)
        .where(ChatMessageRecord.request_id.startswith(SEED_PREFIX))
        .distinct()
    )
    counts: dict[str, int] = {}
    async with engine.begin() as conn:
        counts["chat_sessions"] = int(
            await conn.scalar(
                select(func.count()).select_from(seeded_sessions.subquery())
            )
            or 0
        )
        if apply:
            for model, cond in (
                (LatencyRecord, LatencyRecord.request_id.startswith(SEED_PREFIX)),
                (EvaluationRecord, EvaluationRecord.request_id.startswith(SEED_PREFIX)),
                (ChatSession, ChatSession.id.in_(seeded_sessions)),
                (
                    TestIpConfig,
                    TestIpConfig.description.startswith(SEED_TEST_IP_PREFIX),
                ),
            ):
                result = await conn.execute(delete(model).where(cond))
                counts[model.__tablename__] = int(result.rowcount or 0)
    if apply:
        await run_rollup(engine, rollup_days(cfg))
    return counts


async def seed(
    engine: AsyncEngine | None, cfg: SeedConfig, *, flush_every: int = 5000
) -> Counter[str]:
    started = time.perf_counter()
    kb = await load_kb(engine) if engine is not None else []
    taxonomy = await load_taxonomy(engine) if engine is not None else DEFAULT_TAXONOMY
    if engine is not None and not kb:
        logger.warning("Knowledge base is empty: answers fall back to generic text")
    logger.info(
        "Loaded %d KB chunks; generating %d sessions for %d visitors",
        len(kb),
        cfg.sessions,
        cfg.visitors,
    )

    writer = Writer(engine)
    writer.add(
        Bundle(
            rows={
                **{t: [] for t in TABLES},
                "test_ip_configs": test_ip_rows(random.Random(cfg.seed), cfg.now),
            }
        )
    )
    first = True
    for bundle in generate(cfg, kb, taxonomy):
        if first and engine is not None:
            first = False
            if await already_seeded(engine, bundle.rows["chat_sessions"][0]["id"]):
                logger.info(
                    "Seed %d already applied; nothing to insert (use --purge first)",
                    cfg.seed,
                )
                return Counter()
        writer.add(bundle)
        if writer.pending() >= flush_every:
            await writer.flush()
            logger.info(
                "progress: %d sessions, %d messages",
                writer.counts["chat_sessions"],
                writer.counts["chat_messages"],
            )
    await writer.flush()
    if engine is not None:
        await run_rollup(engine, rollup_days(cfg))
    logger.info("Done in %.1fs", time.perf_counter() - started)
    return writer.counts


_LOCAL_HOSTS = frozenset({"localhost", "host.docker.internal"})
_DEV_HOST_TAGS = ("dev", "local", "test")
OVERRIDE_FLAG = "--i-know-this-is-not-prod"


def is_dev_database(database_url: str) -> bool:
    """True for loopback/private-IP hosts, localhost or a dev/local/test hostname."""
    try:
        host = (make_url(database_url).host or "").strip("[]").lower()
    except ArgumentError:
        return False
    if not host:
        return False  # unix socket / unknown: treat as not provably dev
    if host in _LOCAL_HOSTS:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return any(tag in host for tag in _DEV_HOST_TAGS)
    return address.is_loopback or address.is_private


def production_guard(env: str, database_url: str, *, override: bool) -> str | None:
    """Reason to refuse running, or None. Never includes the URL (it holds secrets)."""
    if override:
        return None
    if env.strip().lower() in ("production", "prod"):
        return f"ENV={env} looks like production; pass {OVERRIDE_FLAG} if it is not"
    if not is_dev_database(database_url):
        return (
            "DATABASE_URL host is not local/dev (loopback, private IP, localhost or "
            f"a dev/local/test hostname); pass {OVERRIDE_FLAG} if it is not prod"
        )
    return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply", action="store_true", help="Write to the database (default: dry run)"
    )
    parser.add_argument(
        "--purge", action="store_true", help="Remove seeded rows instead of adding"
    )
    parser.add_argument(
        "--seed", type=int, default=42, help="RNG seed (deterministic output)"
    )
    parser.add_argument(
        "--scale", type=float, default=1.0, help="Volume multiplier (1.0 ~ 5k sessions)"
    )
    parser.add_argument(
        "--days", type=int, default=DEFAULT_DAYS, help="History window in days"
    )
    parser.add_argument(
        "--eval-rate",
        type=float,
        default=None,
        help="Online-eval sample rate (default: ONLINE_EVAL_SAMPLE_RATE)",
    )
    parser.add_argument(
        OVERRIDE_FLAG,
        dest="not_prod",
        action="store_true",
        help="Bypass the production guard (ENV / database host checks)",
    )
    args = parser.parse_args(argv)
    if not 0.001 <= args.scale <= 20:  # noqa: PLR2004
        parser.error("--scale must be between 0.001 and 20")
    if not 1 <= args.days <= 730:  # noqa: PLR2004
        parser.error("--days must be between 1 and 730")
    if args.eval_rate is not None and not 0 <= args.eval_rate <= 1:
        parser.error("--eval-rate must be between 0 and 1")
    return args


async def amain(args: argparse.Namespace) -> int:
    settings = get_settings()
    refusal = production_guard(
        settings.env, settings.database_url, override=args.not_prod
    )
    if refusal:
        logger.error("Refusing to run: %s", refusal)
        return 2
    cfg = SeedConfig(
        seed=args.seed,
        scale=args.scale,
        days=args.days,
        eval_rate=settings.online_eval_sample_rate
        if args.eval_rate is None
        else args.eval_rate,
    )
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        if args.purge:
            counts = await purge(engine, cfg, apply=args.apply)
            mode = "purged" if args.apply else "DRY RUN, would purge sessions"
            print(f"{mode}: {counts} (audit_log rows are append-only and kept)")
            return 0
        if not args.apply:
            # Read the KB for realistic answers, but write nothing.
            kb = await load_kb(engine)
            counts = Counter()
            for bundle in generate(cfg, kb, await load_taxonomy(engine)):
                for table, rows in bundle.rows.items():
                    counts[table] += len(rows)
            counts["test_ip_configs"] += len(TEST_IP_ENTRIES)
            print(f"DRY RUN (nothing written, KB chunks={len(kb)}): {dict(counts)}")
            return 0
        counts = await seed(engine, cfg)
        print(f"inserted (attempted, ON CONFLICT DO NOTHING): {dict(counts)}")
        return 0
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(amain(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
