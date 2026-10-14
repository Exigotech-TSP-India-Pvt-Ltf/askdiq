"""Unit tests for Parameterized Safe SQL Query Builders & Allow-List Sorting (OWASP A03)."""

from datetime import UTC, datetime
from fastapi import HTTPException
import pytest
from sqlalchemy import select

from app.db.query_builders import (
    apply_sort_order,
    build_date_range_filters,
    build_document_filter_clauses,
    build_feedback_filter_clauses,
    build_governance_filter_clauses,
    build_query_filter_clauses,
    build_search_filter,
    build_session_filter_clauses,
    build_user_filter_clauses,
    escape_like_expression,
)
from app.models.db_models import (
    AuditLog,
    ChatMessageRecord,
    ChatSession,
    Document,
    MessageFeedback,
    User,
)
from app.schemas.filters import (
    DocumentFilterParams,
    FeedbackFilterParams,
    GovernanceFilterParams,
    QueryFilterParams,
    SessionFilterParams,
    UserFilterParams,
)


def test_escape_like_expression():
    """Verify proper escaping of SQL wildcard characters to avoid wildcard injection."""
    assert escape_like_expression("normal text") == "normal text"
    assert escape_like_expression("100%") == "100\\%"
    assert escape_like_expression("user_name") == "user\\_name"
    assert escape_like_expression("path\\to\\file") == "path\\\\to\\\\file"
    assert escape_like_expression("%") == "\\%"
    assert escape_like_expression("_") == "\\_"
    assert escape_like_expression("\\") == "\\\\"
    assert escape_like_expression("%_\\") == "\\%\\_\\\\"


def test_build_date_range_filters():
    """Verify parameterized boundary expressions."""
    start = datetime(2025, 1, 1, tzinfo=UTC)
    end = datetime(2025, 1, 2, tzinfo=UTC)

    # Empty
    assert build_date_range_filters(User.created_at, None, None) == []

    # Start only
    c_start = build_date_range_filters(User.created_at, start, None)
    assert len(c_start) == 1

    # End only
    c_end = build_date_range_filters(User.created_at, None, end)
    assert len(c_end) == 1

    # Both
    c_both = build_date_range_filters(User.created_at, start, end)
    assert len(c_both) == 2


def test_build_search_filter():
    """Verify ILIKE expression construction and wildcard safety."""
    assert build_search_filter([User.email], None) is None
    assert build_search_filter([User.email], "   ") is None

    expr = build_search_filter([User.email, User.full_name], "alice%test")
    assert expr is not None
    # Compiled expression should contain escape character
    sql_str = str(expr.compile())
    assert "ESCAPE" in sql_str.upper() or "ILIKE" in sql_str.upper()


def test_apply_sort_order_valid_and_defaults():
    """Verify safe sort order application from allow-lists."""
    q = select(User)
    allowed_sorts = {
        "created_desc": User.created_at.desc(),
        "created_asc": User.created_at.asc(),
        "email_asc": User.email.asc(),
    }

    # None sort key with default
    q_default = apply_sort_order(
        q, None, allowed_sorts, default_sort=User.created_at.desc()
    )
    assert "ORDER BY users.created_at DESC" in str(q_default)

    # Valid sort key
    q_email = apply_sort_order(q, "email_asc", allowed_sorts)
    assert "ORDER BY users.email ASC" in str(q_email)


def test_apply_sort_order_sql_injection_defense():
    """Verify that unlisted and SQL-injection sort keys are rejected with HTTP 422 (OWASP A03)."""
    q = select(User)
    allowed_sorts = {
        "created_desc": User.created_at.desc(),
    }

    # SQL Injection attempts
    injections = [
        "created_desc; DROP TABLE users--",
        "created_desc; SELECT * FROM audit_log",
        "random_col",
        "1; WAITFOR DELAY '0:0:5'--",
    ]

    for malicious_key in injections:
        with pytest.raises(HTTPException) as exc_info:
            apply_sort_order(q, malicious_key, allowed_sorts)
        assert exc_info.value.status_code == 422
        assert "Invalid sort key" in exc_info.value.detail


def test_build_user_filter_clauses():
    """Verify User filter generation including production/test audience."""
    test_emails = {"qa@example.com", "test@example.com"}

    f_test = UserFilterParams(user_type="test", q="qa")
    clauses = build_user_filter_clauses(User, f_test, test_emails=test_emails)
    assert len(clauses) >= 2

    f_prod = UserFilterParams(user_type="production")
    clauses_prod = build_user_filter_clauses(User, f_prod, test_emails=test_emails)
    assert len(clauses_prod) >= 1


def test_build_session_filter_clauses():
    """Verify ChatSession filter generation with IP and audience scoping."""
    test_ips = {"127.0.0.1", "10.0.0.1"}

    # Scoped to Users (excludes test IPs)
    f = SessionFilterParams(ip="192.168.1.1")
    clauses = build_session_filter_clauses(
        ChatSession, f, test_ips=test_ips, audience="users"
    )
    assert len(clauses) >= 2

    # Scoped to Testing (includes test IPs only)
    clauses_testing = build_session_filter_clauses(
        ChatSession, f, test_ips=test_ips, audience="testing"
    )
    assert len(clauses_testing) >= 2


def test_build_query_filter_clauses():
    """Verify ChatMessageRecord filter generation with economics and outcomes."""
    f = QueryFilterParams(
        status="error",
        outcome="grounded",
        min_cost=0.005,
        max_cost=0.10,
        q="invoice",
    )
    clauses = build_query_filter_clauses(ChatMessageRecord, f)
    assert len(clauses) >= 4


def test_build_feedback_and_governance_clauses():
    """Verify MessageFeedback and AuditLog filter generation."""
    ff = FeedbackFilterParams(rating="positive", q="helpful")
    f_clauses = build_feedback_filter_clauses(MessageFeedback, ff)
    assert len(f_clauses) >= 2

    gf = GovernanceFilterParams(service="guardrail_block")
    g_clauses = build_governance_filter_clauses(AuditLog, gf)
    assert len(g_clauses) >= 1


def test_build_document_filter_clauses():
    """Verify Document filter generation with format filtering."""
    df = DocumentFilterParams(origin_format="pdf", q="handbook")
    d_clauses = build_document_filter_clauses(Document, df)
    assert len(d_clauses) >= 2
