"""Triage PATCH fields the Review Queue drawer sends (corrected answer, cleared note)."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.feedback import FeedbackTriageRequest
from tests.feedback_helpers import make_exchange, make_feedback, make_session


def test_triage_request_tracks_explicit_nulls() -> None:
    body = FeedbackTriageRequest.model_validate({"version": 1, "review_note": None})
    assert "review_note" in body.model_fields_set
    assert "corrected_answer" not in body.model_fields_set
    cleaned = FeedbackTriageRequest(version=1, corrected_answer="ok\x00 answer")
    assert cleaned.corrected_answer == "ok answer"


async def test_corrected_answer_is_saved_masked_and_clearable(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    session = await make_session(db_session, "owner@example.com")
    _u, asst = await make_exchange(db_session, session, turn=1, question="Q?")
    row = await make_feedback(db_session, asst, actor="t@example.com", rating=1)
    url = f"/admin/feedback/{row.id}"

    saved = await client.patch(
        url,
        json={
            "version": 1,
            "corrected_answer": "Email jane.doe@example.com for the policy.",
        },
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["corrected_answer"]
    assert "jane.doe@example.com" not in body["corrected_answer"]
    assert body["triage_status"] == "new"  # an answer alone is not a transition

    noted = await client.patch(url, json={"version": 2, "review_note": "check"})
    assert noted.json()["review_note"] == "check"

    cleared = await client.patch(
        url, json={"version": 3, "review_note": None, "corrected_answer": None}
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["review_note"] is None
    assert cleared.json()["corrected_answer"] is None

    empty = await client.patch(url, json={"version": 4})
    assert empty.status_code == 422
