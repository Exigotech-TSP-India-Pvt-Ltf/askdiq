"""Alerting: rules over persisted rows, dedupe/cooldown, jobs, endpoints.

Database tests run inside the transactional ``db_session``. Rules are
evaluated at a far-future ``NOW`` and rows are seeded at that time, so only
this test's rows fall inside each rule's window.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts import catalogue as c
from app.alerts import jobs as alert_jobs
from app.alerts import rules, service
from app.alerts.catalogue import NotifyStatus, Severity
from app.alerts.email import Brand
from app.alerts.service import Finding
from app.core.config import get_settings
from app.models.db_models import (
    AccessRequest,
    Alert,
    AuditLog,
    ChatMessageRecord,
    ChatSession,
    LatencyRecord,
    MessageFeedback,
)
from tests.auth_helpers import make_user
from tests.pg_helpers import new_engine, require_postgres

NOW = datetime(2099, 3, 15, 12, 0, tzinfo=UTC)
NAIVE_NOW = NOW.replace(tzinfo=None)
BRAND = Brand(product_name="DeployIQ", assistant_name="AskDIQ", company_name="Exigo")


def email_cfg(**overrides):
    values = {
        "smtp_host": "smtp.example.com",
        "alert_email_from": "alerts@example.com",
        "alert_email_to": "ops@example.com",
        "alert_email_min_severity": "info",
        "alert_cooldown_minutes": 60,
    }
    values.update(overrides)
    return get_settings().model_copy(update=values)


def no_email_cfg(**overrides):
    return email_cfg(smtp_host="", **overrides)


def finding(key: str, severity: Severity = Severity.WARNING, **details) -> Finding:
    return Finding(
        rule=c.ERROR_RATE,
        dedupe_key=key,
        severity=severity,
        title=f"Test alert {key}",
        summary="Something happened.",
        component="API",
        details={"value": 5, "threshold": 2, "unit": "%", **details},
    )


def key() -> str:
    return f"test:{uuid.uuid4().hex}"


async def notify_jobs(db: AsyncSession, alert_id: uuid.UUID) -> list[str]:
    rows = await db.execute(
        text(
            "SELECT payload->>'event' FROM jobs WHERE type = :t "
            "AND payload->>'alert_id' = :id ORDER BY created_at"
        ),
        {"t": service.NOTIFY_JOB, "id": str(alert_id)},
    )
    return [r[0] for r in rows]


# --- service: dedupe, escalation, cooldown, resolution --------------------------


async def test_raise_twice_coalesces_into_one_alert(db_session: AsyncSession) -> None:
    k = key()
    first = await service.raise_alert(
        db_session, finding(k), now=NOW, settings=email_cfg()
    )
    second = await service.raise_alert(
        db_session, finding(k), now=NOW + timedelta(minutes=1), settings=email_cfg()
    )
    assert first.event == service.EVENT_OPENED
    assert second.event == service.EVENT_UPDATED
    assert second.alert.id == first.alert.id
    assert second.alert.occurrences == 2
    assert second.alert.last_seen_at == NOW + timedelta(minutes=1)
    count = await db_session.scalar(
        select(func.count(Alert.id)).where(Alert.dedupe_key == k)
    )
    assert count == 1
    assert first.alert.notify_status == NotifyStatus.QUEUED.value
    assert await notify_jobs(db_session, first.alert.id) == ["opened"]


async def test_escalation_updates_severity_and_notifies_again(
    db_session: AsyncSession,
) -> None:
    k = key()
    opened = await service.raise_alert(
        db_session, finding(k), now=NOW, settings=email_cfg()
    )
    escalated = await service.raise_alert(
        db_session, finding(k, Severity.CRITICAL), now=NOW, settings=email_cfg()
    )
    assert escalated.event == service.EVENT_ESCALATED
    assert escalated.alert.severity == "critical"
    assert await notify_jobs(db_session, opened.alert.id) == ["opened", "escalated"]
    # A later, milder reading never downgrades an open alert.
    again = await service.raise_alert(
        db_session, finding(k), now=NOW, settings=email_cfg()
    )
    assert again.event == service.EVENT_UPDATED
    assert again.alert.severity == "critical"


async def test_without_smtp_alert_is_stored_but_not_emailed(
    db_session: AsyncSession,
) -> None:
    result = await service.raise_alert(
        db_session, finding(key()), now=NOW, settings=no_email_cfg()
    )
    assert result.alert.notify_status == NotifyStatus.DISABLED.value
    assert await notify_jobs(db_session, result.alert.id) == []


async def test_below_min_severity_is_skipped(db_session: AsyncSession) -> None:
    result = await service.raise_alert(
        db_session,
        finding(key(), Severity.INFO),
        now=NOW,
        settings=email_cfg(alert_email_min_severity="warning"),
    )
    assert result.alert.notify_status == NotifyStatus.SKIPPED.value
    assert await notify_jobs(db_session, result.alert.id) == []


async def test_cooldown_suppresses_repeat_email_for_same_key(
    db_session: AsyncSession,
) -> None:
    k = key()
    cfg = email_cfg(alert_cooldown_minutes=60)
    first = await service.raise_alert(db_session, finding(k), now=NOW, settings=cfg)
    first.alert.notify_status = NotifyStatus.SENT.value
    first.alert.notified_at = NOW
    await service.resolve_alert(db_session, first.alert, now=NOW, settings=cfg)
    again = await service.raise_alert(
        db_session, finding(k), now=NOW + timedelta(minutes=10), settings=cfg
    )
    assert again.alert.id != first.alert.id
    assert again.event == service.EVENT_OPENED
    assert again.alert.notify_status == NotifyStatus.SUPPRESSED.value
    assert await notify_jobs(db_session, again.alert.id) == []
    # The resolved notification of the first alert was queued (it was emailed).
    assert await notify_jobs(db_session, first.alert.id) == ["opened", "resolved"]
    # After the cooldown the same key is emailed again.
    await service.resolve_alert(db_session, again.alert, now=NOW, settings=cfg)
    later = await service.raise_alert(
        db_session, finding(k), now=NOW + timedelta(minutes=61), settings=cfg
    )
    assert later.alert.notify_status == NotifyStatus.QUEUED.value


async def test_resolve_missing_and_stale(db_session: AsyncSession) -> None:
    kept, gone = key(), key()
    cfg = no_email_cfg()
    await service.raise_alert(db_session, finding(kept), now=NOW, settings=cfg)
    await service.raise_alert(db_session, finding(gone), now=NOW, settings=cfg)
    await service.resolve_missing(
        db_session, {c.ERROR_RATE}, {kept}, now=NOW, settings=cfg
    )
    statuses = dict(
        (
            await db_session.execute(
                select(Alert.dedupe_key, Alert.status).where(
                    Alert.dedupe_key.in_([kept, gone])
                )
            )
        ).all()
    )
    assert statuses == {kept: "open", gone: "resolved"}

    stale = Finding(
        rule=c.AZURE_THROTTLING,
        dedupe_key=key(),
        severity=Severity.WARNING,
        title="429s",
        summary="",
        component="Azure OpenAI",
    )
    res = await service.raise_alert(db_session, stale, now=NOW, settings=cfg)
    resolved = await service.resolve_stale(
        db_session,
        {c.AZURE_THROTTLING},
        NOW + timedelta(minutes=5),
        now=NOW,
        settings=cfg,
    )
    assert resolved >= 1
    assert res.alert.status == "resolved"


async def test_details_are_pii_masked(db_session: AsyncSession) -> None:
    result = await service.raise_alert(
        db_session,
        finding(key(), last_error="failed for jane.citizen@example.com"),
        now=NOW,
        settings=no_email_cfg(),
    )
    assert "jane.citizen@example.com" not in str(result.alert.details)


async def test_concurrent_raisers_create_one_active_alert() -> None:
    """Two "workers" (separate engines) raising the same key at once."""
    engines = [new_engine(), new_engine()]
    await require_postgres(engines[0])
    k = key()
    cfg = no_email_cfg()

    async def raise_on(engine) -> None:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            await service.raise_alert(db, finding(k), now=NOW, settings=cfg)
            await db.commit()

    try:
        await asyncio.gather(*(raise_on(e) for e in engines for _ in range(3)))
        async with engines[0].connect() as conn:
            active, occurrences = (
                await conn.execute(
                    text(
                        "SELECT count(*), max(occurrences) FROM alerts "
                        "WHERE dedupe_key = :k AND status IN ('open', 'acknowledged')"
                    ),
                    {"k": k},
                )
            ).one()
        assert active == 1
        assert occurrences == 6
    finally:
        async with engines[0].begin() as conn:
            await conn.execute(
                text("DELETE FROM alerts WHERE dedupe_key = :k"), {"k": k}
            )
        for engine in engines:
            await engine.dispose()


# --- rules ----------------------------------------------------------------------


def audit(action: str, detail: dict | None = None, minutes_ago: int = 1) -> AuditLog:
    return AuditLog(
        request_id=f"req-{uuid.uuid4().hex[:12]}",
        actor="system",
        action=action,
        detail=detail or {},
        created_at=NAIVE_NOW - timedelta(minutes=minutes_ago),
    )


async def test_error_rate_rule_needs_volume_and_classifies(
    db_session: AsyncSession,
) -> None:
    cfg = get_settings()
    few = [
        audit("server_error", {"path": "/x"}) for _ in range(cfg.alert_min_requests - 1)
    ]
    db_session.add_all(few)
    await db_session.flush()
    assert await rules.error_rate_rule(db_session, NOW, cfg) == []
    db_session.add(audit("server_error", {"path": "/x"}))
    await db_session.flush()
    [found] = await rules.error_rate_rule(db_session, NOW, cfg)
    assert found.severity is Severity.CRITICAL
    assert found.details["counts"]["server_errors"] == cfg.alert_min_requests
    assert found.title.startswith("Error rate 100.0% above")
    assert len(found.details["request_ids"]) == 5


async def test_p95_latency_rule_title(db_session: AsyncSession) -> None:
    cfg = get_settings()
    db_session.add_all(
        LatencyRecord(
            request_id=f"lat-{i}",
            total_ms=24_000.0,
            created_at=NAIVE_NOW - timedelta(minutes=2),
        )
        for i in range(cfg.alert_latency_min_samples)
    )
    await db_session.flush()
    [found] = await rules.p95_latency_rule(db_session, NOW, cfg)
    assert found.severity is Severity.CRITICAL
    assert found.title == "p95 latency 24 s above 20 s"
    assert found.details["value"] == 24000


async def test_llm_failures_rule(db_session: AsyncSession) -> None:
    cfg = get_settings()
    db_session.add_all(
        audit("llm_call_failed", {"error_type": "RateLimitError"})
        for _ in range(cfg.alert_llm_failures_threshold)
    )
    await db_session.flush()
    [found] = await rules.llm_failures_rule(db_session, NOW, cfg)
    assert found.severity is Severity.CRITICAL
    assert found.details["counts"] == {
        "RateLimitError": cfg.alert_llm_failures_threshold
    }


async def _exchange(db: AsyncSession, cost: float = 0.0) -> ChatMessageRecord:
    session = ChatSession(
        user_email="alerts-test@example.com",
        created_at=NAIVE_NOW,
        last_activity_at=NAIVE_NOW,
        expires_at=NAIVE_NOW + timedelta(hours=1),
    )
    db.add(session)
    await db.flush()
    message = ChatMessageRecord(
        session_id=session.id,
        turn_index=1,
        role="assistant",
        content="answer",
        cost_usd=cost,
        created_at=NAIVE_NOW - timedelta(minutes=1),
    )
    db.add(message)
    await db.flush()
    return message


async def test_monthly_budget_rule_warning_then_critical(
    db_session: AsyncSession,
) -> None:
    cfg = get_settings().model_copy(update={"monthly_cost_budget_usd": 100.0})
    await _exchange(db_session, cost=85.0)
    [found] = await rules.monthly_budget_rule(db_session, NOW, cfg)
    assert found.severity is Severity.WARNING
    assert found.dedupe_key == "monthly_budget:2099-03"
    await _exchange(db_session, cost=20.0)
    [found] = await rules.monthly_budget_rule(db_session, NOW, cfg)
    assert found.severity is Severity.CRITICAL
    no_budget = cfg.model_copy(update={"monthly_cost_budget_usd": None})
    assert await rules.monthly_budget_rule(db_session, NOW, no_budget) == []


async def test_feedback_spike_rule(db_session: AsyncSession) -> None:
    cfg = get_settings()
    message = await _exchange(db_session)
    for rating in (1, 2, 5, 5, 1):
        db_session.add(
            MessageFeedback(
                message_id=message.id,
                session_id=message.session_id,
                actor=f"user-{uuid.uuid4().hex[:6]}",
                rating=rating,
                created_at=NAIVE_NOW - timedelta(minutes=5),
                updated_at=NAIVE_NOW - timedelta(minutes=5),
            )
        )
    await db_session.flush()
    [found] = await rules.feedback_spike_rule(db_session, NOW, cfg)
    assert found.details["counts"] == {"ratings": 5, "negative": 3}
    assert found.severity is Severity.WARNING


async def test_access_request_rule_one_alert_per_request(
    db_session: AsyncSession,
) -> None:
    user = await make_user(db_session)
    request = AccessRequest(user_id=user.id, requested_role="operator")
    db_session.add(request)
    await db_session.flush()
    found = await rules.access_request_rule(db_session, NOW, get_settings())
    keys = {f.dedupe_key for f in found}
    assert f"{c.ACCESS_REQUEST}:{request.id}" in keys
    mine = next(f for f in found if f.dedupe_key.endswith(str(request.id)))
    assert mine.severity is Severity.INFO
    # No PII (the requester's email) in the alert.
    assert user.email not in str(mine)


async def test_job_failure_ingestion_and_online_eval_rules(
    db_session: AsyncSession,
) -> None:
    cfg = get_settings()
    job_type = f"test.dead.{uuid.uuid4().hex[:8]}"
    await db_session.execute(
        text(
            "INSERT INTO jobs (type, payload, status, run_after, finished_at, "
            "last_error, attempts, max_attempts) VALUES (:t, '{}'::jsonb, 'dead', "
            ":now, :now, :err, 5, 5)"
        ),
        {"t": job_type, "now": NOW, "err": "boom for jane.citizen@example.com"},
    )
    db_session.add(audit("server_error", {"path": "/ingest/file", "status_code": 500}))
    db_session.add_all(
        audit("online_eval", {"guardrail_flags": ["online_unfaithful"]})
        for _ in range(3)
    )
    await db_session.flush()
    jobs = await rules.job_failures_rule(db_session, NOW, cfg)
    mine = next(f for f in jobs if f.details["job_type"] == job_type)
    assert "jane.citizen@example.com" not in mine.details["last_error"]
    [ingest] = await rules.ingestion_failures_rule(db_session, NOW, cfg)
    assert ingest.details["counts"] == {"failed_uploads": 1}
    [quality] = await rules.online_eval_rule(db_session, NOW, cfg)
    assert quality.severity is Severity.WARNING
    assert quality.details["counts"] == {"online_unfaithful": 3}


# --- jobs: evaluate + notify ----------------------------------------------------


@contextlib.asynccontextmanager
async def _borrow(db: AsyncSession) -> AsyncIterator[AsyncSession]:
    yield db


@pytest.fixture
def jobs_on_test_session(db_session: AsyncSession):
    alert_jobs.set_session_factory(lambda: _borrow(db_session))
    yield db_session
    alert_jobs.set_session_factory(None)


async def test_evaluate_raises_and_resolves(jobs_on_test_session: AsyncSession) -> None:
    db = jobs_on_test_session
    cfg = no_email_cfg()
    db.add_all(
        audit("llm_call_failed", {"error_type": "APITimeoutError"})
        for _ in range(cfg.alert_llm_failures_threshold)
    )
    await db.flush()
    stats = await alert_jobs.evaluate_alerts(now=NOW, settings=cfg)
    assert stats["failed_rules"] == 0
    alert = await db.scalar(
        select(Alert).where(Alert.dedupe_key == c.LLM_FAILURES, Alert.status == "open")
    )
    assert alert is not None
    # Later the window has passed: the condition cleared, so it resolves.
    await alert_jobs.evaluate_alerts(now=NOW + timedelta(hours=2), settings=cfg)
    await db.refresh(alert)
    assert alert.status == "resolved"
    assert alert.resolved_by == service.SYSTEM_ACTOR


async def test_notify_sends_and_records(jobs_on_test_session: AsyncSession) -> None:
    db = jobs_on_test_session
    cfg = email_cfg()
    result = await service.raise_alert(db, finding(key()), now=NOW, settings=cfg)
    with patch.object(alert_jobs, "send_email", new=AsyncMock()) as send:
        status = await alert_jobs.deliver_alert_email(
            result.alert.id, "opened", settings=cfg, brand=BRAND
        )
    assert status == "sent"
    message = send.await_args.args[0]
    assert message["To"] == "ops@example.com"
    assert message["Subject"].startswith("[AskDIQ][WARNING] Test alert")
    plain = message.get_body(preferencelist=("plain",)).get_content()
    # Local time comes from Postgres' tz database (2099-03-15 12:00 UTC = 23:00 AEDT).
    assert "2099-03-15 23:00 AEDT, Australia/Sydney" in plain
    await db.refresh(result.alert)
    assert result.alert.notify_status == "sent"
    assert result.alert.notify_attempts == 1
    assert result.alert.notified_at is not None


async def test_notify_failure_is_recorded_and_reraised(
    jobs_on_test_session: AsyncSession,
) -> None:
    db = jobs_on_test_session
    cfg = email_cfg(alert_email_max_attempts=2)
    result = await service.raise_alert(db, finding(key()), now=NOW, settings=cfg)
    failing = AsyncMock(side_effect=ConnectionRefusedError("smtp down"))
    with patch.object(alert_jobs, "send_email", new=failing):
        with pytest.raises(ConnectionRefusedError):
            await alert_jobs.deliver_alert_email(
                result.alert.id, "opened", settings=cfg, brand=BRAND
            )
        await db.refresh(result.alert)
        assert result.alert.notify_status == "retrying"
        with pytest.raises(ConnectionRefusedError):
            await alert_jobs.deliver_alert_email(
                result.alert.id, "opened", settings=cfg, brand=BRAND
            )
    await db.refresh(result.alert)
    assert result.alert.notify_status == "failed"
    assert result.alert.notify_attempts == 2
    assert "ConnectionRefusedError" in (result.alert.notify_error or "")


async def test_notify_without_smtp_marks_disabled(
    jobs_on_test_session: AsyncSession,
) -> None:
    db = jobs_on_test_session
    result = await service.raise_alert(
        db, finding(key()), now=NOW, settings=email_cfg()
    )
    status = await alert_jobs.deliver_alert_email(
        result.alert.id, "opened", settings=no_email_cfg(), brand=BRAND
    )
    assert status == "disabled"


# --- endpoints ------------------------------------------------------------------


async def test_alert_endpoints_list_ack_resolve(
    client, db_session: AsyncSession
) -> None:
    k = key()
    raised = await service.raise_alert(
        db_session, finding(k, Severity.CRITICAL), now=NOW, settings=no_email_cfg()
    )
    alert_id = raised.alert.id

    res = await client.get("/admin/alerts", params={"rule": c.ERROR_RATE, "limit": 100})
    assert res.status_code == 200
    body = res.json()
    assert any(item["id"] == str(alert_id) for item in body["items"])
    assert body["active_by_severity"]["critical"] >= 1
    item = next(i for i in body["items"] if i["id"] == str(alert_id))
    assert item["console_path"] == "/admin/governance"
    assert item["rule_name"] == "Error rate"
    assert "dedupe_key" not in item

    res = await client.get(f"/admin/alerts/{alert_id}")
    assert res.status_code == 200

    res = await client.post(f"/admin/alerts/{alert_id}/acknowledge")
    assert res.status_code == 200
    assert res.json()["status"] == "acknowledged"
    assert res.json()["acknowledged_by"]

    res = await client.post(f"/admin/alerts/{alert_id}/resolve")
    assert res.status_code == 200
    assert res.json()["status"] == "resolved"

    actions = (
        await db_session.scalars(
            select(AuditLog.action).where(
                AuditLog.detail["alert_id"].as_string() == str(alert_id)
            )
        )
    ).all()
    assert sorted(actions) == ["alert_acknowledged", "alert_resolved"]


async def test_alert_endpoints_validation_and_404(client) -> None:
    missing = uuid.uuid4()
    res = await client.get(f"/admin/alerts/{missing}")
    assert res.status_code == 404
    assert res.json()["code"] == "not_found"
    res = await client.post(f"/admin/alerts/{missing}/resolve")
    assert res.status_code == 404
    res = await client.get("/admin/alerts", params={"limit": 1000})
    assert res.status_code == 422
    assert res.json()["code"] == "validation_error"
    res = await client.get("/admin/alerts", params={"severity": "bogus"})
    assert res.status_code == 422


async def test_alert_rules_catalogue(client) -> None:
    res = await client.get("/admin/alerts/rules")
    assert res.status_code == 200
    body = res.json()
    ids = {r["id"] for r in body["rules"]}
    assert {c.ERROR_RATE, c.LLM_CIRCUIT_OPEN, c.MONTHLY_BUDGET, c.ACCESS_REQUEST} <= ids
    assert isinstance(body["email_enabled"], bool)
