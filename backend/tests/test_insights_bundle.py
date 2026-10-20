"""``GET /admin/insights/bundle``: every widget in one response, from memory.

The bundle is answered from in-memory facts (``app.insights.engine``), not
the per-widget SQL; every part must equal its own endpoint's response on the
49-conversation reference fixture for each filter case, every sort and page,
and the person detail must equal the SQL service's.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.insights import engine
from app.insights.analytics_service import InsightsAnalyticsService
from app.schemas.insights import InsightsFilters
from tests.insights_fixture import FIXTURE_TIMEZONE, sequential_runner
from tests.test_insights_parity import CUSTOM_RANGE, FILTER_CASES, person_id

BASE = "/admin/insights"
WIDGETS = (
    "summary",
    "trend",
    "hourly",
    "flow",
    "topics",
    "quality",
    "dropoff",
    "people",
)
EXTRA_CASES = [
    {"period": "custom", "from": "2026-06-01", "to": "2026-06-01"},
    {"hour": "10"},
    {**CUSTOM_RANGE, "quality": "PART", "hour": "14"},
    {"q": "job"},
    {"audience": "users"},
    {"audience": "testing"},
    {"visitor": "client-203.0.113.7"},
]


@pytest.fixture(autouse=True)
def _fresh_engine():
    engine.clear()
    yield
    engine.clear()


def _params(case: dict[str, str], **extra: str) -> dict[str, str]:
    return {**case, "tz": FIXTURE_TIMEZONE, **extra}


async def _get(client: AsyncClient, path: str, params: dict[str, str]) -> dict:
    response = await client.get(f"{BASE}/{path}", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _without_source(body: dict) -> dict:
    return {k: v for k, v in body.items() if k != "source"}


def _without_questions(page: dict) -> dict:
    """The table page without the embedded question summaries (the SQL
    ``/conversations`` endpoint does not carry them)."""
    items = [{k: v for k, v in r.items() if k != "questions"} for r in page["items"]]
    return {**page, "items": items}


@pytest.mark.parametrize(
    "case", FILTER_CASES + EXTRA_CASES, ids=lambda c: str(c) or "all"
)
async def test_every_part_equals_its_endpoint(insights_client, case):
    bundle = await _get(insights_client, "bundle", _params(case))
    assert bundle["as_of"]
    for widget in WIDGETS:
        expected = await _get(insights_client, widget, _params(case))
        assert _without_source(bundle[widget]) == _without_source(expected), widget
    table = await _get(insights_client, "conversations", _params(case))
    assert _without_questions(bundle["conversations"]) == table
    assert bundle["pending"] == await _get(insights_client, "pending", {})


@pytest.mark.parametrize(
    "sort",
    [
        "conv_id",
        "person",
        "start",
        "user_type",
        "outcome",
        "experience_rating",
        "issue",
        "exchanges",
    ],
)
@pytest.mark.parametrize("direction", ["asc", "desc"])
async def test_conversation_pages_equal_the_sql_order(insights_client, sort, direction):
    for case in ({}, CUSTOM_RANGE):
        for offset in ("0", "10"):
            page = {"sort": sort, "dir": direction, "limit": "10", "offset": offset}
            bundle = await _get(
                insights_client, "bundle", _params(case, parts="conversations", **page)
            )
            expected = await _get(
                insights_client, "conversations", _params(case, **page)
            )
            assert _without_questions(bundle["conversations"]) == expected, (
                case,
                page,
            )
            assert bundle["summary"] is None


async def test_person_detail_equals_the_sql_service(
    insights_client, insights_reference_db
):
    service = InsightsAnalyticsService(
        insights_reference_db, sequential_runner(insights_reference_db)
    )
    for person in ("P01", "P03", "P07"):
        pid = person_id(person)
        for audience in ("overview", "users"):
            filters = InsightsFilters(tz=FIXTURE_TIMEZONE, audience=audience)
            expected = await service.person(pid, filters)
            response = await insights_client.get(
                f"{BASE}/people/{pid}",
                params={"tz": FIXTURE_TIMEZONE, "audience": audience},
            )
            if expected is None:
                assert response.status_code == 404
                continue
            assert response.status_code == 200, response.text
            assert response.json() == expected.model_dump(mode="json")


async def test_rows_embed_their_labelled_questions(insights_client):
    """Each row's questions equal the detail route's labelled turns (masked,
    cut to ``QUESTION_CHARS``): expanding a row needs no request."""
    page = {"limit": "100", "sort": "exchanges", "dir": "desc"}
    body = await _get(
        insights_client, "bundle", _params({}, parts="conversations", **page)
    )
    rows = body["conversations"]["items"]
    assert rows
    for row in rows[:12]:
        detail = await _get(
            insights_client, f"conversations/{row['conv_id']}", _params({})
        )
        labelled = [t for t in detail["turns"] if not t["pending"]]
        assert len(row["questions"]) == len(labelled) == row["exchanges"]
        for q, t in zip(row["questions"], labelled, strict=True):
            assert q["turn"] == t["turn"]
            assert q["time"] == t["timestamp"][11:16]
            assert len(q["text"]) <= engine.QUESTION_CHARS
            if len(t["user_message"]) <= engine.QUESTION_CHARS:
                assert q["text"] == t["user_message"]
            else:
                assert q["text"].endswith(engine.ELLIPSIS)
            for key in (
                "intent",
                "intent_label",
                "substantive",
                "handled",
                "quality",
                "quality_label",
                "issue",
                "issue_label",
                "friction",
            ):
                assert q[key] == t[key], key


async def test_search_finds_a_person_label(insights_client):
    """Searching ``P01`` / ``p1`` also finds that person's conversations."""
    page = {"parts": "conversations", "limit": "100"}
    pid = person_id("P01")
    own = await _get(insights_client, "bundle", _params({"person": pid}, **page))
    for needle in ("P01", "p1"):
        body = await _get(insights_client, "bundle", _params({"q": needle}, **page))
        found = [r for r in body["conversations"]["items"] if r["person_id"] == pid]
        assert len(found) == own["conversations"]["total"] > 0, needle


def test_long_questions_are_masked_then_cut(monkeypatch):
    monkeypatch.setattr(engine, "mask", lambda v: v.replace("secret", "[X]"))
    short = engine._short("a secret")  # noqa: SLF001
    assert short == "a [X]"
    cut = engine._short("secret " + "y" * 400)  # noqa: SLF001
    assert cut.startswith("[X] y")
    assert len(cut) == engine.QUESTION_CHARS
    assert cut.endswith(engine.ELLIPSIS)


async def test_unknown_person_is_404(insights_client):
    response = await insights_client.get(
        f"{BASE}/people/0123456789abcdef", params={"tz": FIXTURE_TIMEZONE}
    )
    assert response.status_code == 404


async def test_revalidation_is_a_304(insights_client):
    first = await insights_client.get(f"{BASE}/bundle", params=_params({}))
    assert first.status_code == 200
    etag = first.headers["etag"]
    assert first.headers["x-data-as-of"]
    again = await insights_client.get(
        f"{BASE}/bundle", params=_params({}), headers={"If-None-Match": etag}
    )
    assert again.status_code == 304
    other = await insights_client.get(
        f"{BASE}/bundle", params=_params({"intent": "SVC"})
    )
    assert other.headers["etag"] != etag


async def test_parts_limit_the_response(insights_client):
    body = await _get(insights_client, "bundle", _params({}, parts="summary"))
    assert body["summary"]["conversations"] == 49
    assert all(body[w] is None for w in (*WIDGETS[1:], "conversations", "pending"))


@pytest.mark.parametrize(
    "params",
    [
        {"parts": "nope"},
        {"period": "custom"},
        {"limit": "0"},
        {"hour": "24"},
        {"intent": "bad code"},
    ],
)
async def test_invalid_parameters_are_422(insights_client, params):
    response = await insights_client.get(f"{BASE}/bundle", params=_params(params))
    assert response.status_code == 422
