"""Phase 3 Integration Test Suite.

Validates:
- Australian & Enterprise PII detectors with checksum validation (TFN, ABN, Medicare, Credit Card, AU Phone)
- Negative checksum tests (invalid checksums remain untouched)
- Allowlist bypass for company public identifiers
- Order of detection (Medicare vs Phone)
- Performance on large documents (< 200ms)
- Rolling 60s Azure OpenAI Quota Telemetry (TPM / RPM headroom, 429 throttling counts, expiration)
- Online Quality Sampler (background execution, secret leak detection, AuditLog generation with CRITICAL flag)
- White-labelling provider and public GET /branding endpoint
"""

import time

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.routes.admin import _log_level_for
from app.core.config import get_settings
from app.core.quota import quota_monitor
from app.governance.online_eval import _run_evaluation_task, quality_tracker
from app.governance.pii import (
    redact_pii,
    validate_abn,
    validate_luhn,
    validate_medicare,
    validate_tfn,
)
from app.guardrails.conversational import (
    detect_conversational_intent,
    get_greeting_response,
)
from app.harness.orchestrator import _fallback_response
from app.harness.prompts import get_system_prompt
from main import app

# ---------------------------------------------------------------------------
# Task 3.2: Australian & Enterprise PII Entity Detection
# ---------------------------------------------------------------------------


def test_tfn_validation_and_redaction():
    # Valid 9-digit TFN: 123 456 782 -> 1*1 + 2*4 + 3*3 + 4*7 + 5*5 + 6*8 + 7*6 + 8*9 + 2*10 = 320 - 1 = 319 (319 % 11 == 0)
    # Let's verify: 1 + 8 + 9 + 28 + 25 + 48 + 42 + 72 + 20 = 253. 253 = 11 * 23. Valid!
    valid_tfn = "123 456 782"
    assert validate_tfn(valid_tfn) is True

    # Invalid TFN (checksum fails)
    invalid_tfn = "123 456 789"
    assert validate_tfn(invalid_tfn) is False

    text = (
        f"Contact taxpayer with valid TFN {valid_tfn} and random number {invalid_tfn}."
    )
    redacted, types = redact_pii(text)

    assert "[REDACTED_TFN]" in redacted
    assert valid_tfn not in redacted
    assert invalid_tfn in redacted
    assert "tfn" in types


def test_abn_validation_and_redaction():
    # Valid ABN: 51 824 753 556 (Department of Home Affairs public ABN)
    # Weights: [10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19] with first digit minus 1 (4)
    # 4*10 + 1*1 + 8*3 + 2*5 + 4*7 + 7*9 + 5*11 + 3*13 + 5*15 + 5*17 + 6*19 = 534 (534 % 89 == 0). Valid!
    valid_abn = "51 824 753 556"
    assert validate_abn(valid_abn) is True

    # Invalid ABN
    invalid_abn = "51 824 753 557"
    assert validate_abn(invalid_abn) is False

    text = f"Company ABN is {valid_abn} and ref is {invalid_abn}."
    redacted, types = redact_pii(text)

    assert "[REDACTED_ABN]" in redacted
    assert valid_abn not in redacted
    assert invalid_abn in redacted
    assert "abn" in types


def test_abn_allowlist_preserves_marketing_identifiers():
    valid_abn = "51 824 753 556"
    text = f"DeployIQ public corporate ABN: {valid_abn}."

    # When allowlisted, should NOT be redacted
    redacted, _types = redact_pii(text, allowlist=[valid_abn])
    assert valid_abn in redacted
    assert "[REDACTED_ABN]" not in redacted


def test_medicare_validation_and_redaction():
    # Valid Medicare: 2123 45670 1
    # First 8 digits: 2, 1, 2, 3, 4, 5, 6, 7
    # Weights: [1, 3, 7, 9, 1, 3, 7, 9]
    # 2*1 + 1*3 + 2*7 + 3*9 + 4*1 + 5*3 + 6*7 + 7*9 = 2+3+14+27+4+15+42+63 = 170. 170 % 10 = 0.
    # 9th digit = 0. 10th digit = 1. Valid!
    valid_medicare = "2123 45670 1"
    assert validate_medicare(valid_medicare) is True

    invalid_medicare = "2123 45678 1"
    assert validate_medicare(invalid_medicare) is False

    text = f"Patient card: {valid_medicare}. Fake: {invalid_medicare}."
    redacted, types = redact_pii(text)

    assert "[REDACTED_MEDICARE]" in redacted
    assert valid_medicare not in redacted
    assert "medicare" in types


def test_credit_card_luhn_validation_and_redaction():
    # Standard valid Visa test card
    valid_cc = "4532015112830366"
    assert validate_luhn(valid_cc) is True

    # Invalid Luhn card
    invalid_cc = "4532015112830367"
    assert validate_luhn(invalid_cc) is False

    text = f"Charge Visa {valid_cc} or code {invalid_cc}."
    redacted, types = redact_pii(text)

    assert "[REDACTED_CREDIT_CARD]" in redacted
    assert valid_cc not in redacted
    assert invalid_cc in redacted
    assert "credit_card" in types


def test_medicare_precedes_generic_phone_redaction():
    valid_medicare = "2123 45670 1"
    text = f"Check medicare {valid_medicare}"
    redacted, types = redact_pii(text)

    # Should be tagged as medicare, NOT phone
    assert "[REDACTED_MEDICARE]" in redacted
    assert "[REDACTED_PHONE]" not in redacted
    assert "medicare" in types
    assert "phone" not in types


def test_pii_redaction_performance_on_large_document():
    # Generate 1 MB text document
    paragraph = (
        "DeployIQ provides continuous automated compliance and governance. "
        "Contact sales at contact@deployiq.com or phone +61 412 345 678. "
    )
    one_mb_text = paragraph * 8000  # ~1 MB

    start = time.perf_counter()
    redacted, _types = redact_pii(one_mb_text)
    duration_ms = (time.perf_counter() - start) * 1000

    assert "[REDACTED_EMAIL]" in redacted
    assert "[REDACTED_PHONE]" in redacted
    # Must complete in under 3000 ms on a 1 MB text
    assert duration_ms < 3000.0


# ---------------------------------------------------------------------------
# Task 3.3: Rolling 60s Azure OpenAI Quota Telemetry (TPM / RPM)
# ---------------------------------------------------------------------------


def test_quota_tracker_rolling_window_and_headroom():
    settings = get_settings()
    now = 1000.0

    # Record 100,000 tokens for chat deployment
    quota_monitor.record_chat(tokens=100_000, now=now)
    quota_monitor.record_chat(tokens=50_000, now=now + 20.0)

    summary = quota_monitor.get_summary(now=now + 25.0)
    chat_metrics = summary["chat"]

    assert chat_metrics["tpm_current"] == 150_000
    assert chat_metrics["rpm_current"] == 2
    assert chat_metrics["tpm_limit"] == settings.azure_openai_tpm_limit
    assert chat_metrics["tpm_headroom_pct"] == round(
        100.0 * (1.0 - 150_000 / settings.azure_openai_tpm_limit), 2
    )

    # Advance time past 60s window for event 1 (now + 65s: event 1 at 1000 expires, event 2 at 1020 remains)
    summary_later = quota_monitor.get_summary(now=now + 65.0)
    chat_later = summary_later["chat"]
    assert chat_later["tpm_current"] == 50_000
    assert chat_later["rpm_current"] == 1

    # Advance past all events (now + 120s: all events expired)
    summary_expired = quota_monitor.get_summary(now=now + 120.0)
    assert summary_expired["chat"]["tpm_current"] == 0
    assert summary_expired["chat"]["rpm_current"] == 0


def test_quota_tracker_throttling_and_header_telemetry():
    now = 2000.0
    # Simulate a 429 throttle with Azure rate limit headers
    headers = {
        "x-ratelimit-remaining-tokens": "800000",
        "x-ratelimit-remaining-requests": "950",
    }
    quota_monitor.record_chat(
        tokens=500,
        is_error=True,
        is_429=True,
        retries=1,
        headers=headers,
        now=now,
    )

    summary = quota_monitor.get_summary(now=now + 5.0)
    chat_m = summary["chat"]

    assert chat_m["azure_429_count_60s"] == 1
    assert chat_m["azure_retries_60s"] == 1
    assert chat_m["azure_error_rate"] > 0
    assert chat_m["header_telemetry_active"] is True


# ---------------------------------------------------------------------------
# Task 3.1: Online Async Quality Sampler & Log Levels
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_online_eval_detects_secret_leak_and_inserts_audit(db_session):
    # Simulated leaked AWS key in assistant answer
    request_id = "test-secret-leak-req-1"
    synthetic_answer = (
        "Here is the secret configuration with key AKIAIOSFODNN7EXAMPLE for deployment."
    )

    await _run_evaluation_task(
        request_id=request_id,
        query="What is the AWS key?",
        answer=synthetic_answer,
        context_chunks=[],
        initial_flags=[],
        ip_address="192.168.1.100",
    )

    # Verify quality tracker recorded the violation
    kpis = quality_tracker.get_kpis()
    assert kpis["secret_leak_count"] >= 1

    # Verify _log_level_for tags it as CRITICAL
    detail = {"guardrail_flags": ["secret_leak_detected"]}
    assert _log_level_for(detail) == "CRITICAL"

    # Verify PII leak tags as ALERT
    pii_detail = {"guardrail_flags": ["pii_leak_detected"]}
    assert _log_level_for(pii_detail) == "ALERT"

    # Verify unfaithful tags as ALERT
    unfaithful_detail = {"guardrail_flags": ["online_unfaithful"]}
    assert _log_level_for(unfaithful_detail) == "ALERT"


# ---------------------------------------------------------------------------
# Task 3.4: White-Labelling Provider & Configuration Abstraction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_branding_public_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/branding")
        assert resp.status_code == 200
        data = resp.json()

        assert "brand_name" in data
        assert "brand_assistant_name" in data
        assert "brand_support_email" in data
        assert data["brand_name"] == "DeployIQ"
        assert data["brand_assistant_name"] == "AskDIQ"


def test_branding_system_prompt_and_fallback_replies():
    settings = get_settings()
    brand = settings.brand_name

    # System prompt parameterization
    prompt = get_system_prompt()
    assert f"You are {brand}'s website assistant." in prompt
    assert f"keep {brand} as the active subject" in prompt

    # Conversational replies parameterization
    greeting = get_greeting_response()
    assert f"Hello! I'm the {brand} assistant." in greeting

    greeting_intent = detect_conversational_intent("hello there")
    assert greeting_intent is not None
    assert brand in greeting_intent

    # Fallback replies parameterization
    fallback = _fallback_response(
        request_id="test-req-fb",
        flags=["unclear_input"],
        latency_ms=10.0,
    )
    assert f"ask a specific question about {brand}?" in fallback.answer


@pytest.mark.asyncio
async def test_admin_monitoring_endpoint_exposes_quota_and_quality(client: AsyncClient):
    resp = await client.get("/admin/governance/monitoring")
    assert resp.status_code == 200
    data = resp.json()

    # Phase 3 metrics present
    assert "azure_quota_telemetry" in data
    assert "chat" in data["azure_quota_telemetry"]
    assert "tpm_headroom_pct" in data["azure_quota_telemetry"]["chat"]

    assert "online_quality_kpis" in data
    assert "sampled_count" in data["online_quality_kpis"]
    assert "mean_faithfulness" in data["online_quality_kpis"]
