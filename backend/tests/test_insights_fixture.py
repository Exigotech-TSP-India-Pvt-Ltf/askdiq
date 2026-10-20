"""Parity fixture: extractor and loader."""

from __future__ import annotations

import json
from collections import Counter

import pytest
from sqlalchemy import text

from scripts.extract_insights_fixture import (
    FixtureExtractionError,
    build_fixture,
    extract_embed,
)
from tests.insights_fixture import (
    FIXTURE_EMAIL_DOMAIN,
    issue_codes,
    load_reference,
    load_reference_fixture,
    session_id,
)
from tests.pg_helpers import new_engine, require_postgres

# ---------------------------------------------------------------------------
# Extractor (pure; the 510 KB HTML is not committed, so use a synthetic page)
# ---------------------------------------------------------------------------

_CONV = {"conv_id": "C01", "person": "P01", "outcome": "Served"}
_EXCH = {"conv_id": "C01", "turn": "1", "user_message": "a ]}; </script> b"}


def _page(embed: str) -> str:
    return (
        "<html><head><script>var x=1;</script></head><body>"
        f"<script>window.__EMBED__={embed};window.dash={{}};</script>"
        "<script>function render(){}</script></body></html>"
    )


def test_extractor_reads_unquoted_top_level_keys_without_eval():
    embed = (
        "{conversations:"
        + json.dumps([_CONV])
        + ",exchanges:"
        + json.dumps([_EXCH])
        + "}"
    )
    data = extract_embed(_page(embed))
    assert data == {"conversations": [_CONV], "exchanges": [_EXCH]}


def test_extractor_builds_counts():
    embed = "{conversations:" + json.dumps([_CONV]) + ",exchanges:[]}"
    fixture = build_fixture(_page(embed), "x.html")
    assert fixture["conversation_count"] == 1
    assert fixture["exchange_count"] == 0
    assert fixture["source"] == "x.html"


@pytest.mark.parametrize(
    "embed",
    [
        "",  # no marker
        "{conversations:[{}],exchanges:alert(1)}",  # not JSON
        "{conversations:{},exchanges:[]}",  # not an array
        '{conversations:[{"a":{"nested":1}}],exchanges:[]}',  # not flat
        '{conversations:["x"],exchanges:[]}',  # not objects
    ],
)
def test_extractor_rejects_malformed_or_executable_input(embed):
    html = _page(embed) if embed else "<html></html>"
    with pytest.raises(FixtureExtractionError):
        extract_embed(html)


# ---------------------------------------------------------------------------
# Committed fixture values (match the HTML renderer's numbers)
# ---------------------------------------------------------------------------


def test_fixture_has_49_conversations_and_117_exchanges():
    data = load_reference()
    assert data["conversation_count"] == len(data["conversations"]) == 49
    assert data["exchange_count"] == len(data["exchanges"]) == 117


def test_fixture_value_counts():
    data = load_reference()
    outcomes = Counter(c["outcome"] for c in data["conversations"])
    assert outcomes == {
        "Served": 11,
        "Partly served": 14,
        "Not served": 22,
        "Greeting only": 2,
    }
    exchanges = data["exchanges"]
    assert sum(e["handled"] == "Yes" for e in exchanges) == 62
    assert Counter(e["intent"] for e in exchanges)["SVC"] == 33
    quality = Counter(e["bot_quality"] for e in exchanges)
    assert quality["PART"] == 37
    assert quality["OK"] == 32


def test_every_fixture_issue_maps_to_a_taxonomy_code():
    codes = issue_codes()
    labels = {e["issue"] for e in load_reference()["exchanges"] if e["issue"]}
    assert labels <= set(codes)


def test_fixture_names_are_masked():
    texts = " ".join(e["user_message"] for e in load_reference()["exchanges"])
    assert "[name]" in texts
    assert "@" not in texts


# ---------------------------------------------------------------------------
# Loader (Postgres; runs in CI)
# ---------------------------------------------------------------------------


@pytest.fixture
async def pg_ready():
    engine = new_engine()
    try:
        await require_postgres(engine)
    finally:
        await engine.dispose()


async def _counts(db_session) -> dict[str, int]:
    params = {"domain": f"%@{FIXTURE_EMAIL_DOMAIN}"}
    row = (
        await db_session.execute(
            text(
                """
                SELECT
                  (SELECT count(*) FROM chat_sessions WHERE user_email LIKE :domain)
                      AS sessions,
                  (SELECT count(*) FROM chat_messages m
                     JOIN chat_sessions s ON s.id = m.session_id
                    WHERE s.user_email LIKE :domain AND m.role = 'user') AS users,
                  (SELECT count(*) FROM turn_insights ti
                     JOIN chat_sessions s ON s.id = ti.session_id
                    WHERE s.user_email LIKE :domain) AS insights,
                  (SELECT count(*) FROM turn_insights ti
                     JOIN chat_sessions s ON s.id = ti.session_id
                    WHERE s.user_email LIKE :domain AND ti.handled) AS handled
                """
            ),
            params,
        )
    ).one()
    return dict(row._mapping)


async def test_loader_inserts_49_sessions_and_117_user_turns(pg_ready, db_session):
    inserted = await load_reference_fixture(db_session)
    assert inserted["sessions"] == 49
    assert inserted["messages"] == 2 * 117
    assert inserted["turn_insights"] == 117
    counts = await _counts(db_session)
    assert counts == {"sessions": 49, "users": 117, "insights": 117, "handled": 62}


async def test_loader_is_idempotent(pg_ready, db_session):
    await load_reference_fixture(db_session)
    again = await load_reference_fixture(db_session)
    assert again == {"taxonomy": 0, "sessions": 0, "messages": 0, "turn_insights": 0}
    counts = await _counts(db_session)
    assert counts["sessions"] == 49
    assert counts["users"] == 117


async def test_loader_keeps_html_codes_and_local_times(pg_ready, db_session):
    await load_reference_fixture(db_session)
    quality = dict(
        (
            await db_session.execute(
                text(
                    "SELECT ti.bot_quality, count(*) FROM turn_insights ti "
                    "JOIN chat_sessions s ON s.id = ti.session_id "
                    "WHERE s.user_email LIKE :d GROUP BY 1"
                ),
                {"d": f"%@{FIXTURE_EMAIL_DOMAIN}"},
            )
        ).all()
    )
    assert quality["PART"] == 37
    assert quality["OK"] == 32
    first = load_reference()["conversations"][0]
    local_start = await db_session.scalar(
        text(
            "SELECT to_char((created_at AT TIME ZONE 'UTC') AT TIME ZONE "
            "'Australia/Sydney', 'YYYY-MM-DD HH24:MI:SS') FROM chat_sessions "
            "WHERE id = :id"
        ),
        {"id": session_id(first["conv_id"])},
    )
    assert local_start == first["start"]
