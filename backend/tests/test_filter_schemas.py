"""Unit tests for Multi-Dimension Filter Request Schemas (OWASP A03/A04)."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas.filters import (
    BaseFilterParams,
    DateRangeFilter,
    DocumentFilterParams,
    FeedbackFilterParams,
    GovernanceFilterParams,
    PaginationParams,
    QueryFilterParams,
    SessionFilterParams,
    UserFilterParams,
)


def test_pagination_params_defaults_and_boundaries():
    """Verify default values and valid/invalid pagination boundaries."""
    # Defaults
    p = PaginationParams()
    assert p.limit == 10
    assert p.offset == 0

    # Valid boundaries
    p_min = PaginationParams(limit=1, offset=0)
    assert p_min.limit == 1
    assert p_min.offset == 0

    p_max = PaginationParams(limit=200, offset=10000)
    assert p_max.limit == 200
    assert p_max.offset == 10000

    # Violations: limit < 1
    with pytest.raises(ValidationError):
        PaginationParams(limit=0)

    # Violations: limit > 200
    with pytest.raises(ValidationError):
        PaginationParams(limit=201)

    # Violations: offset < 0
    with pytest.raises(ValidationError):
        PaginationParams(offset=-1)

    # Violations: offset > 10000
    with pytest.raises(ValidationError):
        PaginationParams(offset=10001)


def test_date_range_filter():
    """Verify start/end date logic and chronologic consistency."""
    now = datetime.now(UTC)
    earlier = datetime(2025, 1, 1, tzinfo=UTC)
    later = datetime(2025, 1, 2, tzinfo=UTC)

    # Both None
    dr = DateRangeFilter()
    assert dr.start_date is None
    assert dr.end_date is None

    # Valid range
    dr_valid = DateRangeFilter(start_date=earlier, end_date=later)
    assert dr_valid.start_date == earlier
    assert dr_valid.end_date == later

    # Equal start and end is allowed
    dr_equal = DateRangeFilter(start_date=now, end_date=now)
    assert dr_equal.start_date == dr_equal.end_date

    # Inverted range raises ValidationError
    with pytest.raises(
        ValidationError, match="end_date cannot be earlier than start_date"
    ):
        DateRangeFilter(start_date=later, end_date=earlier)


def test_base_filter_query_sanitization():
    """Verify whitespace stripping and empty query conversion."""
    f1 = BaseFilterParams(q="  admin user  ")
    assert f1.q == "admin user"

    f2 = BaseFilterParams(q="    ")
    assert f2.q is None

    f3 = BaseFilterParams(q=None)
    assert f3.q is None

    # Length violation > 200
    with pytest.raises(ValidationError):
        BaseFilterParams(q="a" * 201)


def test_user_filter_params():
    """Verify user filter parameters and literal validation."""
    uf = UserFilterParams(status="active", user_type="production")
    assert uf.status == "active"
    assert uf.user_type == "production"

    with pytest.raises(ValidationError):
        UserFilterParams(status="superuser")

    with pytest.raises(ValidationError):
        UserFilterParams(user_type="unknown")


def test_session_filter_params():
    """Verify session filter parameters and IP address validation."""
    sf = SessionFilterParams(ip="192.168.1.100")
    assert sf.ip == "192.168.1.100"

    with pytest.raises(ValidationError):
        SessionFilterParams(ip="x" * 46)


def test_query_filter_params_cost_validation():
    """Verify min/max cost range and execution status literals."""
    qf = QueryFilterParams(status="success", min_cost=0.01, max_cost=0.05)
    assert qf.status == "success"
    assert qf.min_cost == 0.01
    assert qf.max_cost == 0.05

    # Violate negative cost
    with pytest.raises(ValidationError):
        QueryFilterParams(min_cost=-0.01)

    with pytest.raises(ValidationError):
        QueryFilterParams(max_cost=-1.0)

    # Inverted cost range raises ValidationError
    with pytest.raises(ValidationError, match="max_cost cannot be less than min_cost"):
        QueryFilterParams(min_cost=1.00, max_cost=0.50)

    # Invalid status
    with pytest.raises(ValidationError):
        QueryFilterParams(status="pending")


def test_feedback_filter_params():
    """Verify user feedback rating literals."""
    ff = FeedbackFilterParams(rating="positive")
    assert ff.rating == "positive"

    ff_star = FeedbackFilterParams(rating="5")
    assert ff_star.rating == "5"

    with pytest.raises(ValidationError):
        FeedbackFilterParams(rating="6")


def test_governance_filter_params():
    """Verify governance level literals."""
    gf = GovernanceFilterParams(level="critical", service="retrieval")
    assert gf.level == "critical"
    assert gf.service == "retrieval"

    with pytest.raises(ValidationError):
        GovernanceFilterParams(level="verbose")


def test_document_filter_params():
    """Verify document origin, quarantine, and health status literals."""
    df = DocumentFilterParams(
        origin_format="pdf", quarantine_status="clean", health_status="healthy"
    )
    assert df.origin_format == "pdf"
    assert df.quarantine_status == "clean"
    assert df.health_status == "healthy"

    with pytest.raises(ValidationError):
        DocumentFilterParams(quarantine_status="infected")

    with pytest.raises(ValidationError):
        DocumentFilterParams(health_status="failing")
