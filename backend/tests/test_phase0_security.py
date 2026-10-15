"""Phase 0 security, audit immutability, client attribution, and configuration hardening tests."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.config import get_settings
from app.governance.auth import sanitize_client_id
from app.governance.pii import redact_pii
from app.models.db_models import AuditLog


def test_sanitize_client_id():
    """Subtask 0.2.2: Validate client ID sanitization."""
    from app.governance.auth import DEFAULT_ACTOR

    # Valid UUID
    valid_uuid = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"
    assert sanitize_client_id(valid_uuid) == valid_uuid

    # Valid alphanumeric
    valid_alnum = "client_abc-123"
    assert sanitize_client_id(valid_alnum) == valid_alnum

    # Path traversal / script injection attempts normalize safely to DEFAULT_ACTOR
    assert sanitize_client_id("../../../etc/passwd") == DEFAULT_ACTOR
    assert sanitize_client_id("<script>alert(1)</script>") == DEFAULT_ACTOR
    assert sanitize_client_id("admin' OR '1'='1") == DEFAULT_ACTOR

    # Oversized string (>64 chars)
    oversized = "a" * 65
    assert sanitize_client_id(oversized) == DEFAULT_ACTOR

    # Empty string
    assert sanitize_client_id("") == DEFAULT_ACTOR
    assert sanitize_client_id(None) == DEFAULT_ACTOR


@pytest.mark.asyncio
async def test_audit_log_immutability(db_session):
    """Subtask 0.5: Enforce append-only on audit_log at database level."""
    # 1. INSERT must succeed
    new_log = AuditLog(
        request_id="test-req-immutable-01",
        actor="test@example.com",
        action="test_action",
        ip_address="127.0.0.1",
        detail={"event": "initial"},
    )
    db_session.add(new_log)
    await db_session.flush()
    assert new_log.id is not None

    # 2. UPDATE must be blocked by the trigger
    with pytest.raises(DBAPIError) as exc_update:
        await db_session.execute(
            text("UPDATE audit_log SET action = 'tampered' WHERE id = :id"),
            {"id": new_log.id},
        )
    assert "audit_log is append-only" in str(exc_update.value)

    # Roll back the failed update within the transaction savepoint
    await db_session.rollback()

    # Re-insert in fresh transaction
    new_log2 = AuditLog(
        request_id="test-req-immutable-02",
        actor="test@example.com",
        action="test_action_2",
        ip_address="127.0.0.1",
        detail={"event": "initial_2"},
    )
    db_session.add(new_log2)
    await db_session.flush()

    # 3. DELETE must be blocked by the trigger
    with pytest.raises(DBAPIError) as exc_delete:
        await db_session.execute(
            text("DELETE FROM audit_log WHERE id = :id"),
            {"id": new_log2.id},
        )
    assert "audit_log is append-only" in str(exc_delete.value)


def test_pii_redaction_in_audit_detail():
    """Subtask 0.4: Redact sensitive PII before recording in audit detail."""
    raw_query = "Please email John at john.doe@company.com or check TFN 123 456 789"
    redacted, found_pii = redact_pii(raw_query)

    assert "john.doe@company.com" not in redacted
    assert "[REDACTED_EMAIL]" in redacted
    assert len(found_pii) > 0
    assert "email" in found_pii


def test_production_config_hardening():
    """Subtask 0.3: Configuration hardening defaults and settings."""
    settings = get_settings()

    # CORS origins list
    assert isinstance(settings.cors_allowed_origins, list)
    assert "http://localhost:3000" in settings.cors_allowed_origins

    # Verified Azure limits
    assert settings.azure_openai_tpm_limit == 1_000_000
    assert settings.azure_openai_rpm_limit == 1_000
    assert settings.azure_openai_embedding_tpm_limit == 1_000_000

    # Pricing defaults present
    assert settings.price_embed_per_million > 0
    assert settings.price_prompt_per_million > 0
    assert settings.price_output_per_million > 0

    # Rollout flags
    assert hasattr(settings, "ingest_server_conversion")
    assert hasattr(settings, "online_eval_enabled")
