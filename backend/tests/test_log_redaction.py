"""Visitor text in log lines is redacted (app.retrieval.retriever.log_safe_text)."""

from __future__ import annotations

from app.retrieval.retriever import log_safe_text


def test_phone_email_and_self_introduced_name_are_redacted() -> None:
    text = (
        "My name is John Smith, email john.smith@acme.com.au, phone 0412 345 678. "
        "Can someone call me?"
    )
    safe = log_safe_text(text)
    for leaked in ("John", "Smith", "john.smith@acme.com.au", "0412 345 678"):
        assert leaked not in safe
    assert "[REDACTED_EMAIL]" in safe
    assert "[REDACTED_PHONE]" in safe
    assert "[REDACTED_NAME]" in safe


def test_redaction_happens_before_the_cap() -> None:
    text = "x" * 50 + " call 0412 345 678"
    assert "0412" not in log_safe_text(text, 60)
    assert len(log_safe_text(text, 60)) <= 60


def test_plain_questions_are_unchanged() -> None:
    assert log_safe_text("What is Zero Trust?") == "What is Zero Trust?"
    assert log_safe_text("") == ""
