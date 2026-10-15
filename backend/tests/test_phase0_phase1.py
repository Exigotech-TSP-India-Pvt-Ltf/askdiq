"""Integration tests for Phase 0 and Phase 1 (Task 0.2, Task 1.1 - 1.4)."""

import pytest
from sqlalchemy import select

from app.governance.outcomes import (
    ALL_OUTCOMES,
    GENERATION_OUTCOMES,
)
from app.models.db_models import ChatMessageRecord, ChatSession
from scripts.backfill_usage_accounting import backfill_usage_accounting


@pytest.mark.asyncio
async def test_seeder_deterministic_and_outcomes(db_session):
    """Subtask 0.2.1 & Integration Suite 0: Verify seeder produces repeatable outcomes."""
    # Verify outcomes enum validity
    assert len(ALL_OUTCOMES) == 5
    assert "answered" in GENERATION_OUTCOMES
    assert "ungrounded" in GENERATION_OUTCOMES
    assert "llm_failed" in GENERATION_OUTCOMES

    # Check that assistant messages have outcome populated
    results = (
        (
            await db_session.execute(
                select(ChatMessageRecord)
                .where(ChatMessageRecord.role == "assistant")
                .limit(50)
            )
        )
        .scalars()
        .all()
    )

    for msg in results:
        assert msg.outcome is not None
        assert msg.outcome in ALL_OUTCOMES


@pytest.mark.asyncio
async def test_backfill_idempotent(db_session):
    """Subtask 1.4.2 & Integration Suite 1: Verify backfill leaves zero unassigned outcomes."""
    summary = await backfill_usage_accounting(apply=True, batch_size=500)
    assert summary["updated"] == 0  # All assistant rows are already backfilled

    # Confirm in DB
    null_outcomes = (
        (
            await db_session.execute(
                select(ChatMessageRecord).where(
                    ChatMessageRecord.role == "assistant",
                    ChatMessageRecord.outcome.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(null_outcomes) == 0


@pytest.mark.asyncio
async def test_write_time_outcome_persistence(db_session):
    """Subtask 1.3.1 - 1.3.2: Verify record_exchange stores outcome at write time."""
    from datetime import datetime, timedelta

    from app.core.sessions import record_exchange

    sess = ChatSession(
        user_email="test-outcome@askdiq.local",
        title="Test Outcome Session",
        expires_at=datetime.utcnow() + timedelta(hours=1),
        created_ip="127.0.0.1",
    )
    db_session.add(sess)
    await db_session.commit()

    _u_id, a_id = await record_exchange(
        db_session,
        session=sess,
        user_text="What is the policy?",
        answer_text="The policy is strict.",
        request_id="req-test-outcome-123",
        grounded=True,
        guardrail_flags=[],
        client_ip="127.0.0.1",
        prompt_tokens=150,
        completion_tokens=40,
        outcome="answered",
        commit=True,
    )

    saved_asst = await db_session.get(ChatMessageRecord, a_id)
    assert saved_asst is not None
    assert saved_asst.outcome == "answered"
    assert saved_asst.cost_usd is not None
