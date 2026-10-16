"""Unit and integration tests for User Feedback Submission API & Schemas (Task 1.3)."""

import uuid

import pytest
from pydantic import ValidationError

from app.core import sessions as session_service
from app.models.db_models import (
    ChatMessageRecord,
)
from app.schemas.session import UserFeedbackRequest


def test_user_feedback_schema_validation():
    """Subtask 1.3.1: Validates rating 1-5, optional comment, control character stripping."""
    # Valid feedback with optional comment
    req = UserFeedbackRequest(rating=5, comment="Great answer!\x00\x08")
    assert req.rating == 5
    assert req.comment == "Great answer!"

    # Comment is strictly optional
    req_no_comment = UserFeedbackRequest(rating=3, comment=None)
    assert req_no_comment.rating == 3
    assert req_no_comment.comment is None

    # Rating boundary checks
    with pytest.raises(ValidationError):
        UserFeedbackRequest(rating=0)

    with pytest.raises(ValidationError):
        UserFeedbackRequest(rating=6)


@pytest.mark.asyncio
async def test_feedback_submission_and_upsert(client, db_session):
    """Subtask 1.3.2 & 1.3.3: Submit feedback on assistant message, verify upsert and audit logging."""
    actor = "test_user@example.com"
    client.headers["X-Client-Id"] = actor

    # 1. Create a session
    sess_summary = await session_service.create_session(
        db_session, user_email=actor, title="Feedback Test", client_ip="198.51.100.25"
    )
    sess_id = sess_summary.id

    # 2. Add a user turn and an assistant turn
    user_msg = ChatMessageRecord(
        session_id=uuid.UUID(sess_id),
        turn_index=1,
        role="user",
        content="What is AskDIQ?",
        ip_address="198.51.100.25",
    )
    assistant_msg = ChatMessageRecord(
        session_id=uuid.UUID(sess_id),
        turn_index=1,
        role="assistant",
        content="AskDIQ is an enterprise compliance RAG assistant.",
        ip_address="198.51.100.25",
        request_id="req-feedback-trace-1",
    )
    db_session.add_all([user_msg, assistant_msg])
    await db_session.commit()

    user_mid = str(user_msg.id)
    asst_mid = str(assistant_msg.id)

    # 3. Submitting feedback on user message must fail (404)
    res_user = await client.post(
        f"/sessions/{sess_id}/messages/{user_mid}/feedback",
        json={"rating": 5, "comment": "Feedback on question should fail"},
    )
    assert res_user.status_code == 404

    # 4. Submitting feedback by non-owner must fail (404)
    client.headers["X-Client-Id"] = "intruder@example.com"
    res_intruder = await client.post(
        f"/sessions/{sess_id}/messages/{asst_mid}/feedback",
        json={"rating": 1, "comment": "Unauthorized rating"},
    )
    assert res_intruder.status_code == 404

    # 5. Owner submits feedback on assistant message -> 200 OK
    client.headers["X-Client-Id"] = actor
    res_ok = await client.post(
        f"/sessions/{sess_id}/messages/{asst_mid}/feedback",
        json={"rating": 4, "comment": "Helpful and clear."},
    )
    assert res_ok.status_code == 200
    data = res_ok.json()
    assert data["message_id"] == asst_mid
    assert data["session_id"] == sess_id
    assert data["rating"] == 4
    assert data["comment"] == "Helpful and clear."

    # 6. Upsert semantics: Owner changes rating to 5 with updated comment
    res_update = await client.post(
        f"/sessions/{sess_id}/messages/{asst_mid}/feedback",
        json={"rating": 5, "comment": "Updated: Perfect answer!"},
    )
    assert res_update.status_code == 200
    data_up = res_update.json()
    assert data_up["rating"] == 5
    assert data_up["comment"] == "Updated: Perfect answer!"

    # 7. Verify session detail returns feedback_rating survives page reload
    sess_detail = await client.get(f"/sessions/{sess_id}")
    assert sess_detail.status_code == 200
    messages = sess_detail.json()["messages"]
    asst_m = next(m for m in messages if m["id"] == asst_mid)
    assert asst_m["feedback_rating"] == 5
    # Saved answers keep their trace link (chat "View trace" after reload).
    assert asst_m["request_id"] == "req-feedback-trace-1"

    # 8. Verify GET /admin/feedback returns de-duplicated latest feedback entry
    admin_res = await client.get("/admin/feedback")
    assert admin_res.status_code == 200
    feedbacks = admin_res.json()
    fb_items = (
        feedbacks.get("items", feedbacks) if isinstance(feedbacks, dict) else feedbacks
    )
    msg_entries = [f for f in fb_items if f.get("message_id") == asst_mid]
    # De-duplicated by message_id -> exactly 1 entry with the updated rating
    assert len(msg_entries) == 1
    assert msg_entries[0]["rating"] == 5
    assert msg_entries[0]["conversation_id"] == sess_id

    # 9. Verify GET /admin/users includes real feedback_count > 0
    users_res = await client.get("/admin/users")
    assert users_res.status_code == 200
    user_data = users_res.json()
    user_list = (
        user_data.get("items", user_data) if isinstance(user_data, dict) else user_data
    )
    assert len(user_list) >= 1
    assert any(u.get("feedback_count", 0) >= 1 for u in user_list)


@pytest.mark.asyncio
async def test_feedback_is_completely_optional(client, db_session):
    """Confirm user feedback is completely optional: queries and messages function normally without any feedback."""
    actor = "normal_user@example.com"
    client.headers["X-Client-Id"] = actor

    sess = await session_service.create_session(
        db_session, user_email=actor, title="Unrated Chat", client_ip="127.0.0.1"
    )
    asst_msg = ChatMessageRecord(
        session_id=uuid.UUID(sess.id),
        turn_index=1,
        role="assistant",
        content="Here is some useful information without needing feedback.",
        ip_address="127.0.0.1",
    )
    db_session.add(asst_msg)
    await db_session.commit()

    # Detail call works seamlessly, feedback_rating is simply None
    detail = await client.get(f"/sessions/{sess.id}")
    assert detail.status_code == 200
    msg = detail.json()["messages"][0]
    assert msg["content"] == "Here is some useful information without needing feedback."
    assert msg["feedback_rating"] is None


@pytest.mark.asyncio
async def test_feedback_rate_limit(client, db_session):
    """Subtask 1.3.2: Exceeding feedback rate limit triggers HTTP 429."""
    from app.core.rate_limit import _feedback_ip_log, _feedback_user_log

    actor = "unique_spammer@example.com"
    client.headers["X-Client-Id"] = actor

    _feedback_user_log.clear()
    _feedback_ip_log.clear()

    ip = "10.0.0.99"
    sess = await session_service.create_session(
        db_session, user_email=actor, title="Spam Session", client_ip=ip
    )
    asst_msg = ChatMessageRecord(
        session_id=uuid.UUID(sess.id),
        turn_index=1,
        role="assistant",
        content="Sample response.",
        ip_address=ip,
    )
    db_session.add(asst_msg)
    await db_session.commit()
    mid = str(asst_msg.id)

    # Submit 30 feedbacks -> all succeed
    for i in range(1, 31):
        res = await client.post(
            f"/sessions/{sess.id}/messages/{mid}/feedback",
            json={"rating": (i % 5) + 1},
        )
        assert res.status_code == 200

    # 31st feedback in same minute -> 429
    res_429 = await client.post(
        f"/sessions/{sess.id}/messages/{mid}/feedback",
        json={"rating": 5},
    )
    assert res_429.status_code == 429
    assert "Rate limit exceeded" in res_429.json()["detail"]


@pytest.mark.asyncio
async def test_feedback_analytics_and_promote_to_test(client, db_session):
    """Test closed-loop feedback: analytics calculation and golden test case promotion."""
    from app.core.rate_limit import _feedback_ip_log, _feedback_user_log

    _feedback_user_log.clear()
    _feedback_ip_log.clear()

    actor = "admin_user@example.com"
    client.headers["X-Client-Id"] = actor

    sess = await session_service.create_session(
        db_session,
        user_email=actor,
        title="Regression Session",
        client_ip="192.168.1.50",
    )
    user_msg = ChatMessageRecord(
        session_id=uuid.UUID(sess.id),
        turn_index=1,
        role="user",
        content="How do I configure mTLS for Zero Trust?",
        ip_address="192.168.1.50",
    )
    asst_msg = ChatMessageRecord(
        session_id=uuid.UUID(sess.id),
        turn_index=1,
        role="assistant",
        content="I could not find information about mTLS configuration in the provided documents.",
        ip_address="192.168.1.50",
        grounded=False,
        outcome="ungrounded",
    )
    db_session.add_all([user_msg, asst_msg])
    await db_session.commit()

    asst_mid = str(asst_msg.id)

    # Submit negative feedback rating
    fb_res = await client.post(
        f"/sessions/{sess.id}/messages/{asst_mid}/feedback",
        json={"rating": 1, "comment": "Missing documentation on mTLS setup."},
    )
    assert fb_res.status_code == 200

    # 1. Test GET /admin/feedback/analytics
    analytics_res = await client.get("/admin/feedback/analytics")
    assert analytics_res.status_code == 200
    analytics_data = analytics_res.json()
    assert analytics_data["total_feedback"] >= 1
    assert analytics_data["negative_count"] >= 1
    assert "negative_ratio" in analytics_data
    assert "feedback_by_outcome" in analytics_data

    # 2. Test POST /admin/feedback/{mid}/promote-to-test
    promote_res = await client.post(f"/admin/feedback/{asst_mid}/promote-to-test")
    assert promote_res.status_code == 200
    promote_data = promote_res.json()
    assert promote_data["status"] == "promoted"
    assert "case_id" in promote_data
    assert promote_data["query"] == "How do I configure mTLS for Zero Trust?"


async def _assistant_turn(db_session, actor: str) -> tuple[str, str]:
    sess = await session_service.create_session(
        db_session, user_email=actor, title="Details", client_ip="198.51.100.30"
    )
    msg = ChatMessageRecord(
        session_id=uuid.UUID(sess.id),
        turn_index=1,
        role="assistant",
        content="An answer.",
        ip_address="198.51.100.30",
    )
    db_session.add(msg)
    await db_session.commit()
    return sess.id, str(msg.id)


@pytest.mark.asyncio
async def test_feedback_comment_is_pii_masked_before_storage(client, db_session):
    from sqlalchemy import select

    from app.models.db_models import MessageFeedback

    actor = "masked_comment@example.com"
    client.headers["X-Client-Id"] = actor
    sess_id, mid = await _assistant_turn(db_session, actor)

    res = await client.post(
        f"/sessions/{sess_id}/messages/{mid}/feedback",
        json={"rating": 1, "comment": "Email me at jane.doe@example.org please"},
    )
    assert res.status_code == 200
    assert "jane.doe@example.org" not in res.json()["comment"]

    stored = await db_session.scalar(
        select(MessageFeedback.comment).where(
            MessageFeedback.message_id == uuid.UUID(mid)
        )
    )
    assert stored is not None
    assert "jane.doe@example.org" not in stored


@pytest.mark.asyncio
async def test_note_without_details_keeps_stored_reasons_on_thumbs_down(
    client, db_session
):
    actor = "keep_reasons@example.com"
    client.headers["X-Client-Id"] = actor
    sess_id, mid = await _assistant_turn(db_session, actor)
    url = f"/sessions/{sess_id}/messages/{mid}/feedback"

    first = await client.post(
        url,
        json={
            "rating": 1,
            "reason_tags": ["wrong"],
            "corrected_answer": "The right answer.",
        },
    )
    assert first.status_code == 200

    # Reloaded page: the note is saved without the (unknown) details.
    note = await client.post(url, json={"rating": 1, "comment": "Also outdated."})
    assert note.status_code == 200
    body = note.json()
    assert body["reason_tags"] == ["wrong"]
    assert body["corrected_answer"] == "The right answer."
    assert body["comment"] == "Also outdated."

    # Explicitly sent empty details still clear them.
    cleared = await client.post(
        url, json={"rating": 1, "reason_tags": [], "corrected_answer": None}
    )
    assert cleared.json()["reason_tags"] == []
    assert cleared.json()["corrected_answer"] is None

    # A positive rating never keeps "what was wrong" details.
    await client.post(url, json={"rating": 1, "reason_tags": ["wrong"]})
    up = await client.post(url, json={"rating": 5})
    assert up.json()["reason_tags"] == []
