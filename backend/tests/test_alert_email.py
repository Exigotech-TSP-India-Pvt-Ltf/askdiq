"""Alert email rendering, escaping, SMTP delivery and process sampling (no DB)."""

from __future__ import annotations

import smtplib
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr, ValidationError

from app.alerts import catalogue as c
from app.alerts import sampler as sampler_mod
from app.alerts.email import (
    AlertView,
    Brand,
    build_message,
    describe_error,
    format_when,
    render_alert_email,
    send_email,
    utc_key,
)
from app.alerts.sampler import AlertSampler, quota_findings
from app.core.config import Settings, get_settings

WHEN = datetime(2026, 10, 19, 5, 4, tzinfo=UTC)
BRAND = Brand(
    product_name="DeployIQ",
    assistant_name="AskDIQ",
    company_name="Exigo Tech",
    primary_hex="#123456",
)


def cfg(**overrides) -> Settings:
    values = {
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_username": "mailer",
        "alert_email_from": "AskDIQ Alerts <alerts@example.com>",
        "alert_email_to": "ops@example.com, oncall@example.com",
        "app_base_url": "https://askdiqui.example.com/",
    }
    values.update(overrides)
    return get_settings().model_copy(update=values)


def view(**overrides) -> AlertView:
    values = {
        "id": "8d3f6a1e-0000-4000-8000-000000000001",
        "rule": c.P95_LATENCY,
        "severity": "critical",
        "status": "open",
        "title": "p95 latency 24 s above 20 s",
        "summary": "95% of the last 40 chat requests finished within 24 s.",
        "component": "Query pipeline",
        "details": {
            "value": 24000,
            "threshold": 20000.0,
            "unit": "ms",
            "window_minutes": 60,
            "counts": {"requests": 40},
            "request_ids": ["req-1", "req-2"],
        },
        "occurrences": 3,
        "first_seen_at": WHEN,
        "last_seen_at": WHEN,
        "local_times": {utc_key(WHEN): "2026-10-19 16:04 AEDT"},
    }
    values.update(overrides)
    return AlertView(**values)


def test_subject_and_plain_text_contain_the_essentials() -> None:
    rendered = render_alert_email(view(), BRAND, cfg(), "opened")
    assert rendered.subject == "[AskDIQ][CRITICAL] p95 latency 24 s above 20 s"
    text = rendered.text
    for expected in (
        "What happened: 95% of the last 40 chat requests",
        "CRITICAL",
        "Query pipeline",
        "2026-10-19 05:04 UTC",
        "Australia/Sydney",
        "24000 ms",
        "20000.0 ms",
        "last 60 minutes",
        "requests: 40",
        "req-1, req-2",
        "https://askdiqui.example.com/admin/governance?tab=Monitoring",
        "Runbook:",
    ):
        assert expected in text, expected


def test_html_is_table_based_inline_styled_and_branded() -> None:
    html = render_alert_email(view(), BRAND, cfg(), "opened").html
    assert html.startswith("<!DOCTYPE html>")
    assert 'role="presentation"' in html
    assert "<style" not in html  # inline CSS only (Outlook / Gmail)
    assert "background:#123456" in html  # brand primary colour
    assert "#B42318" in html  # critical badge
    assert 'href="https://askdiqui.example.com/admin/governance?tab=Monitoring"' in html
    assert "DeployIQ alert" in html


def test_dynamic_values_are_escaped_and_masked() -> None:
    hostile = view(
        title='<script>alert("x")</script> latency',
        summary="<img src=x onerror=alert(1)> for jane.citizen@example.com",
        details={"last_error": "<b>boom</b>\r\nBcc: evil@example.com"},
    )
    rendered = render_alert_email(hostile, BRAND, cfg(), "opened")
    assert "<script>" not in rendered.html
    assert "&lt;script&gt;" in rendered.html
    assert "<img" not in rendered.html
    assert "<b>boom</b>" not in rendered.html
    assert "jane.citizen@example.com" not in rendered.html
    assert "jane.citizen@example.com" not in rendered.text
    # No header injection through the subject.
    assert "\n" not in rendered.subject and "\r" not in rendered.subject
    message = build_message(rendered, cfg())
    assert message["Bcc"] is None


def test_resolved_and_escalated_subjects() -> None:
    resolved = render_alert_email(
        view(status="resolved", resolved_at=WHEN), BRAND, cfg(), "resolved"
    )
    assert resolved.subject.startswith("[AskDIQ][RESOLVED] ")
    assert "Resolved:" in resolved.text
    escalated = render_alert_email(view(), BRAND, cfg(), "escalated")
    assert "[CRITICAL] Escalated: " in escalated.subject


def test_multipart_message_headers() -> None:
    message = build_message(render_alert_email(view(), BRAND, cfg(), "opened"), cfg())
    assert message.is_multipart()
    types = [part.get_content_type() for part in message.iter_parts()]
    assert types == ["text/plain", "text/html"]
    assert message["To"] == "ops@example.com, oncall@example.com"
    assert message["From"] == "AskDIQ Alerts <alerts@example.com>"
    assert message["Auto-Submitted"] == "auto-generated"
    assert message["Message-ID"].endswith("@example.com>")


async def test_send_uses_starttls_and_login() -> None:
    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    settings = cfg(smtp_password=SecretStr("s3cret"))
    message = build_message(
        render_alert_email(view(), BRAND, settings, "opened"), settings
    )
    with patch("app.alerts.email.smtplib.SMTP", return_value=smtp) as factory:
        await send_email(message, settings)
    factory.assert_called_once_with("smtp.example.com", 587, timeout=15.0)
    smtp.starttls.assert_called_once()
    smtp.login.assert_called_once_with("mailer", "s3cret")
    smtp.send_message.assert_called_once_with(message)


async def test_send_implicit_tls() -> None:
    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    settings = cfg(smtp_ssl=True, smtp_port=465, smtp_username="")
    message = build_message(
        render_alert_email(view(), BRAND, settings, "opened"), settings
    )
    with patch("app.alerts.email.smtplib.SMTP_SSL", return_value=smtp) as factory:
        await send_email(message, settings)
    assert factory.call_args.args == ("smtp.example.com", 465)
    smtp.starttls.assert_not_called()
    smtp.login.assert_not_called()


def test_describe_error_never_leaks_credentials() -> None:
    auth = smtplib.SMTPAuthenticationError(535, b"bad password s3cret")
    assert describe_error(auth) == "SMTP authentication failed (code 535)"
    assert describe_error(TimeoutError("timed out")) == "TimeoutError: timed out"


def test_format_when_shows_utc_and_local_time() -> None:
    local = {utc_key(WHEN): "2026-10-19 16:04 AEDT"}
    out = format_when(WHEN, "Australia/Sydney", local)
    assert out == "2026-10-19 05:04 UTC (2026-10-19 16:04 AEDT, Australia/Sydney)"
    # Unknown zone and no precomputed value: UTC only, never an error.
    assert format_when(WHEN, "Nowhere/Land") == "2026-10-19 05:04 UTC"


def test_email_settings_validation() -> None:
    base = {
        "database_url": "postgresql+asyncpg://u:p@localhost/db",
        "azure_openai_api_key": "k",
        "azure_openai_endpoint": "https://x",
        "azure_openai_api_version": "v",
        "azure_deployment_name": "d",
        "session_secret": "x" * 40,
        "auth_dev_mock": True,
        "env": "development",
    }
    ok = Settings(
        **base,
        smtp_host="smtp.x.com",
        alert_email_from="a@x.com",
        alert_email_to="b@x.com,c@x.com",
    )
    assert ok.alert_email_configured
    assert ok.alert_email_recipients == ["b@x.com", "c@x.com"]
    assert not Settings(**base).alert_email_configured
    with pytest.raises(ValidationError):
        Settings(**base, alert_email_to="ops@x.com\r\nBcc: evil@x.com")
    with pytest.raises(ValidationError):
        Settings(**base, alert_email_to="not-an-email")


# --- process sampling -------------------------------------------------------------


def _quota(util_headroom: float, throttled: int) -> dict:
    metrics = {
        "tpm_headroom_pct": util_headroom,
        "rpm_headroom_pct": 100.0,
        "azure_429_count_60s": throttled,
        "tpm_current": 1,
        "rpm_current": 1,
    }
    return {"chat": metrics, "embedding": {**metrics, "azure_429_count_60s": 0}}


def test_quota_and_throttling_findings() -> None:
    settings = get_settings()
    assert quota_findings(_quota(50.0, 0), settings) == []
    found = {f.rule: f for f in quota_findings(_quota(3.0, 4), settings)}
    assert found[c.AZURE_QUOTA].severity == "critical"
    assert found[c.AZURE_THROTTLING].severity == "warning"
    many = quota_findings(
        _quota(50.0, settings.alert_azure_429_threshold * 5), settings
    )
    assert many[0].severity == "critical"


def test_circuit_finding_when_open() -> None:
    breaker = sampler_mod.get_llm_breaker()
    breaker.reset()
    assert sampler_mod.circuit_finding() is None
    for _ in range(breaker.failure_threshold):
        breaker.record_failure()
    try:
        found = sampler_mod.circuit_finding()
        assert found is not None
        assert found.rule == c.LLM_CIRCUIT_OPEN
        assert found.severity == "critical"
    finally:
        breaker.reset()


class _Bus:
    def __init__(self) -> None:
        import asyncio  # noqa: PLC0415

        self.started = True
        self.connected = asyncio.Event()
        self.connect_count = 2


def test_event_bus_finding_after_grace() -> None:
    clock = [0.0]
    bus = _Bus()
    sampler = AlertSampler(bus=bus, clock=lambda: clock[0])  # type: ignore[arg-type]
    assert sampler.event_bus_finding() is None
    clock[0] = sampler.cfg.alert_event_bus_grace_seconds + 1
    found = sampler.event_bus_finding()
    assert found is not None
    assert found.rule == c.EVENT_BUS_DOWN
    bus.connected.set()
    assert sampler.event_bus_finding() is None


async def test_database_outage_emails_directly_once_per_cooldown() -> None:
    sent: list = []

    async def fake_send(message, _cfg) -> None:
        sent.append(message)

    clock = [0.0]
    sampler = AlertSampler(
        settings=cfg(alert_cooldown_minutes=10),
        bus=_Bus(),  # type: ignore[arg-type]
        clock=lambda: clock[0],
        send=fake_send,
    )
    sampler.bus.connected.set()

    async def down() -> None:
        raise ConnectionRefusedError("db down")

    with patch.object(sampler_mod, "probe_database", new=down):
        await sampler.sample_once()
        await sampler.sample_once()
        clock[0] = 601.0
        await sampler.sample_once()
    assert len(sent) == 2
    assert sent[0]["Subject"] == "[AskDIQ][CRITICAL] Database unreachable"
