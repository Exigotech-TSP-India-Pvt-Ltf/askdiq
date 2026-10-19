"""Parity with the reference HTML's renderer (planchanges4 B6.4.2, newplan 6.4).

``Reference`` below is a line-by-line Python port of the HTML's ``passConv()``,
``scope()``, ``metrics()``, ``trend()``, ``radial()``, ``flow()``,
``treemap()``, ``donut()``, ``lollipop()`` and ``bubble()`` over the HTML's
own embedded data. Every endpoint must return the same numbers on the
49-conversation fixture, for the "all" period, custom ranges and each filter.

One documented rule difference: the HTML labels C10 "Staff / people lookup"
although its top intent group holds only 3 of 6 substantive turns; our
``user_type`` rule (newplan 6.4) needs 60 %, so C10 is "Mixed" here.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import date, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.core.cache import dashboard_cache
from app.core.config import get_settings
from app.dashboard.snapshots import delete_snapshots
from app.insights.analytics_repository import person_salt
from app.insights.repository import InsightsRepository
from tests.insights_fixture import (
    FIXTURE_TIMEZONE,
    issue_codes,
    load_reference,
    person_email,
    session_id,
)

BASE = "/admin/insights"
USER_TYPE_RULE_DEVIATIONS = {"C10": "Mixed"}
DONUT_ORDER = ("OK", "CORR", "SCOPE", "PART", "FB", "CLAR", "OFF", "FALSE")
OUT = ("Served", "Partly served", "Not served")
CUSTOM_RANGE = {"period": "custom", "from": "2026-05-15", "to": "2026-06-30"}


def person_id(person: str) -> str:
    salt = person_salt(get_settings().session_secret.get_secret_value())
    return hashlib.sha256((salt + person_email(person)).encode()).hexdigest()[:16]


def add_days(day: str, n: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=n)).isoformat()


class Reference:
    """Port of the HTML renderer (``S`` = the filter state)."""

    def __init__(self) -> None:
        data = load_reference()
        self.conv = [
            {
                **c,
                "user_type": USER_TYPE_RULE_DEVIATIONS.get(
                    c["conv_id"], c["user_type"]
                ),
            }
            for c in data["conversations"]
        ]
        self.exch = data["exchanges"]
        self.exby: dict[str, list[dict]] = {}
        for e in self.exch:
            self.exby.setdefault(e["conv_id"], []).append(e)
        starts = sorted(c["start"][:10] for c in self.conv)
        last = sorted(e["timestamp"][:10] for e in self.exch)
        self.min = min(starts[0], last[0])
        self.max = max(starts[-1], last[-1])
        self.issue_label = {code: label for label, code in issue_codes().items()}
        self.person_by_id = {person_id(c["person"]): c["person"] for c in self.conv}

    @staticmethod
    def is_sub(e: dict) -> bool:
        return e["intent"] not in ("GRE", "ACK")

    def eff(self, s: dict) -> tuple[str, str]:
        return s.get("from") or self.min, s.get("to") or self.max

    def pass_conv(self, c: dict, s: dict) -> bool:
        lo, hi = self.eff(s)
        if not lo <= c["start"][:10] <= hi:
            return False
        if s.get("user_type") and c["user_type"] != s["user_type"]:
            return False
        if s.get("outcome") and c["outcome"] != s["outcome"]:
            return False
        if s.get("rating") and c["experience_rating"] != s["rating"]:
            return False
        if s.get("issue") and c["issue"] != self.issue_label[s["issue"]]:
            return False
        if s.get("person") and c["person"] != self.person_by_id[s["person"]]:
            return False
        if s.get("q"):
            hay = " ".join(
                [
                    c["conv_id"],
                    c["person"],
                    c["user_type"],
                    c["issue"],
                    c["failing_question"],
                    c["questions_asked"],
                ]
            ).lower()
            if s["q"].lower() not in hay:
                return False
        if s.get("intent") or s.get("quality"):
            return any(
                (not s.get("intent") or e["intent"] == s["intent"])
                and (not s.get("quality") or e["bot_quality"] == s["quality"])
                for e in self.exby.get(c["conv_id"], [])
            )
        return True

    def scope(self, s: dict) -> tuple[list[dict], list[dict]]:
        lo, hi = self.eff(s)
        convs = [c for c in self.conv if self.pass_conv(c, s)]
        ids = {c["conv_id"] for c in convs}
        exch = [
            e
            for e in self.exch
            if e["conv_id"] in ids
            and lo <= e["timestamp"][:10] <= hi
            and (not s.get("intent") or e["intent"] == s["intent"])
            and (not s.get("quality") or e["bot_quality"] == s["quality"])
        ]
        return convs, exch

    def metrics(self, s: dict) -> dict[str, Any]:
        convs, exch = self.scope(s)
        active = [c for c in convs if c["outcome"] != "Greeting only"]
        subs = [e for e in exch if self.is_sub(e)]
        handled = sum(e["handled"] == "Yes" for e in subs)
        served = sum(c["outcome"] == "Served" for c in convs)
        sum_sub = sum(int(c["substantive_exchanges"] or 0) for c in active)
        return {
            "conversations": len(convs),
            "people": len({c["person"] for c in convs}),
            "substantive_questions": len(subs),
            "handled_questions": handled,
            "served_conversations": served,
            "active_conversations": len(active),
            "handled_pct": 100 * handled / len(subs) if subs else None,
            "served_pct": 100 * served / len(active) if active else None,
            "avg_questions_per_conversation": sum_sub / len(active) if active else None,
        }

    def trend(self, s: dict) -> list[dict]:
        convs, exch = self.scope(s)
        lo, hi = self.eff(s)
        out, day = [], lo
        while day <= hi and len(out) < 400:
            out.append(
                {
                    "day": day,
                    "questions": sum(
                        1 for e in exch if e["timestamp"][:10] == day and self.is_sub(e)
                    ),
                    "conversations": sum(1 for c in convs if c["start"][:10] == day),
                }
            )
            day = add_days(day, 1)
        return out

    def radial(self, s: dict) -> list[int]:
        hours = [0] * 24
        for e in self.scope(s)[1]:
            if self.is_sub(e):
                hours[int(e["timestamp"][11:13])] += 1
        return hours

    def flow(self, s: dict) -> Counter:
        convs = [c for c in self.scope(s)[0] if c["outcome"] != "Greeting only"]
        return Counter((c["user_type"], c["outcome"]) for c in convs)

    def treemap(self, s: dict) -> dict[str, tuple[int, int]]:
        subs = [e for e in self.scope(s)[1] if self.is_sub(e)]
        out: dict[str, tuple[int, int]] = {}
        for code in {e["intent"] for e in subs}:
            rows = [e for e in subs if e["intent"] == code]
            out[code] = (len(rows), sum(e["handled"] == "Yes" for e in rows))
        return out

    def donut(self, s: dict) -> list[tuple[str, int]]:
        counts = Counter(e["bot_quality"] for e in self.scope(s)[1] if self.is_sub(e))
        return [(k, counts[k]) for k in DONUT_ORDER if counts[k]]

    def lollipop(self, s: dict) -> Counter:
        return Counter(
            c["issue"]
            for c in self.scope(s)[0]
            if c["issue"] and c["outcome"] in ("Partly served", "Not served")
        )

    def bubble(self, s: dict) -> dict[str, tuple[int, str]]:
        per: dict[str, dict[str, int]] = {}
        for c in self.scope(s)[0]:
            per.setdefault(c["person"], {})
            per[c["person"]][c["outcome"]] = per[c["person"]].get(c["outcome"], 0) + 1
        out = {}
        for person, counts in per.items():
            best, dom = -1, "Served"
            for k, v in counts.items():
                if v > best:
                    best, dom = v, k
            out[person] = (sum(counts.values()), dom)
        return out


REF = Reference()

FILTER_CASES: list[dict[str, str]] = [
    {},
    CUSTOM_RANGE,
    {"period": "custom", "from": "2026-08-01", "to": "2026-08-31"},
    {"intent": "SVC"},
    {"quality": "PART"},
    {"intent": "CAR", "quality": "OFF"},
    {"outcome": "Not served"},
    {"rating": "Great"},
    {"user_type": "Career seeker"},
    {"issue": "JOBS"},
    {"q": "cloud"},
    {"person": person_id("P03")},
    {**CUSTOM_RANGE, "intent": "SVC", "outcome": "Partly served"},
]


def _params(case: dict[str, str]) -> dict[str, str]:
    return {**case, "tz": FIXTURE_TIMEZONE}


async def _get(client: AsyncClient, path: str, case: dict[str, str]) -> dict:
    response = await client.get(f"{BASE}/{path}", params=_params(case))
    assert response.status_code == 200, response.text
    return response.json()


def _close(actual: float | None, expected: float | None) -> bool:
    if expected is None:
        return actual is None
    return actual == pytest.approx(expected, abs=0.01)


# ---------------------------------------------------------------------------
# Headline numbers (newplan 6.4)
# ---------------------------------------------------------------------------


async def test_outcome_counts_match_the_html(insights_client):
    flow = await _get(insights_client, "flow", {})
    outcomes = {n["name"]: n["conversations"] for n in flow["outcomes"]}
    assert outcomes == {"Served": 11, "Partly served": 14, "Not served": 22}
    greeting = await _get(
        insights_client, "conversations", {"outcome": "Greeting only"}
    )
    assert greeting["total"] == 2


async def test_handled_exchanges_and_top_drop_issue(insights_client):
    summary = await _get(insights_client, "summary", {})
    assert summary["exchanges"] == 117
    assert summary["handled_exchanges"] == 62
    dropoff = await _get(insights_client, "dropoff", {})
    top = dropoff["items"][0]
    assert top["label"] == "No live job openings"
    assert top["conversations"] == 13


async def test_intent_and_quality_counts(insights_client):
    topics = await _get(insights_client, "topics", {})
    svc = next(i for i in topics["items"] if i["intent"] == "SVC")
    assert svc["questions"] == 33
    quality = await _get(insights_client, "quality", {})
    counts = {i["quality"]: i["questions"] for i in quality["items"]}
    assert counts["PART"] == 37
    assert counts["OK"] == 32
    assert [i["quality"] for i in quality["items"]] == [
        q for q in DONUT_ORDER if q in counts
    ]


async def test_conversation_fields_match_the_html(insights_client):
    response = await _get(insights_client, "conversations", {"limit": "100"})
    assert response["total"] == 49
    rows = {r["conv_id"]: r for r in response["items"]}
    for conv in REF.conv:
        row = rows[str(session_id(conv["conv_id"]))]
        assert row["person"] == conv["person"], conv["conv_id"]
        assert row["person_id"] == person_id(conv["person"])
        assert row["user_type"] == conv["user_type"], conv["conv_id"]
        assert row["outcome"] == conv["outcome"], conv["conv_id"]
        assert row["experience_rating"] == conv["experience_rating"], conv["conv_id"]
        assert (row["issue_label"] or "") == conv["issue"], conv["conv_id"]
        assert row["exchanges"] == int(conv["exchanges"])
        assert row["substantive_exchanges"] == int(conv["substantive_exchanges"])
        assert row["start"].replace("T", " ")[:19] == conv["start"]
    text_dump = str(response)
    assert "@" not in text_dump
    assert "203.0.113." not in text_dump


# ---------------------------------------------------------------------------
# Every renderer function, for every filter case
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", FILTER_CASES, ids=lambda c: str(c) or "all")
async def test_kpi_strip_equals_metrics(insights_client, case):
    summary = await _get(insights_client, "summary", case)
    expected = REF.metrics(case)
    for key, value in expected.items():
        assert _close(summary[key], value), (key, summary[key], value)


@pytest.mark.parametrize("case", FILTER_CASES, ids=lambda c: str(c) or "all")
async def test_charts_equal_renderer(insights_client, case):
    trend = await _get(insights_client, "trend", case)
    assert [
        {
            "day": d["day"],
            "questions": d["questions"],
            "conversations": d["conversations"],
        }
        for d in trend["days"]
    ] == REF.trend(case)

    hourly = await _get(insights_client, "hourly", case)
    assert hourly["hours"] == REF.radial(case)

    flow = await _get(insights_client, "flow", case)
    assert Counter(
        {
            (link["user_type"], link["outcome"]): link["conversations"]
            for link in flow["links"]
        }
    ) == REF.flow(case)

    topics = await _get(insights_client, "topics", case)
    assert {
        i["intent"]: (i["questions"], i["handled"]) for i in topics["items"]
    } == REF.treemap(case)

    quality = await _get(insights_client, "quality", case)
    assert [(i["quality"], i["questions"]) for i in quality["items"]] == REF.donut(case)

    dropoff = await _get(insights_client, "dropoff", case)
    assert Counter(
        {i["label"]: i["conversations"] for i in dropoff["items"]}
    ) == REF.lollipop(case)

    people = await _get(insights_client, "people", case)
    assert {
        REF.person_by_id[p["person_id"]]: (p["conversations"], p["dominant_outcome"])
        for p in people["items"]
    } == REF.bubble(case)

    conversations = await _get(
        insights_client, "conversations", {**case, "limit": "100"}
    )
    expected_ids = {str(session_id(c["conv_id"])) for c in REF.scope(case)[0]}
    assert {r["conv_id"] for r in conversations["items"]} == expected_ids
    assert conversations["total"] == len(expected_ids)


async def test_intent_filter_keeps_conversation_if_any_turn_matches(insights_client):
    """passConv(): C01 has one SVC turn among others; it stays in scope, and
    only its SVC turns are counted (scope() narrows the exchange set too)."""
    rows = await _get(
        insights_client, "conversations", {"intent": "SVC", "limit": "100"}
    )
    ids = {r["conv_id"] for r in rows["items"]}
    mixed = [
        c["conv_id"]
        for c in REF.conv
        if any(e["intent"] == "SVC" for e in REF.exby[c["conv_id"]])
        and any(e["intent"] != "SVC" for e in REF.exby[c["conv_id"]])
    ]
    assert mixed
    assert {str(session_id(c)) for c in mixed} <= ids
    summary = await _get(insights_client, "summary", {"intent": "SVC"})
    assert summary["substantive_questions"] == 33


# ---------------------------------------------------------------------------
# Closed days from the rollup give the same numbers as the live path
# ---------------------------------------------------------------------------


async def test_rollup_path_matches_live_path(insights_client, insights_reference_db):
    case = {"intent": "SVC"}
    live = {
        path: await _get(insights_client, path, case)
        for path in ("summary", "trend", "topics", "quality")
    }
    assert {v["source"] for v in live.values()} == {"live"}

    db = insights_reference_db
    await db.execute(
        text("UPDATE turn_insights SET classified_at = now() - interval '1 day'")
    )
    days = [
        date.fromisoformat(REF.min) + timedelta(days=i)
        for i in range(
            (date.fromisoformat(REF.max) - date.fromisoformat(REF.min)).days + 1
        )
    ]
    await InsightsRepository(db).recompute_days(days, FIXTURE_TIMEZONE, [])
    # Aggregates are precomputed: drop them, as a refresh after new data would.
    await delete_snapshots(db, "insights.")
    dashboard_cache.clear()
    rolled = {
        path: await _get(insights_client, path, case)
        for path in ("summary", "trend", "topics", "quality")
    }
    assert {v["source"] for v in rolled.values()} == {"rollup"}
    for path, body in rolled.items():
        live[path].pop("source")
        body.pop("source")
        assert body == live[path], path

    # A conversation-level filter cannot use the rollup.
    filtered = await _get(insights_client, "summary", {"outcome": "Served"})
    assert filtered["source"] == "live"
