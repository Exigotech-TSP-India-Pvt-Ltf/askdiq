"""Parameterized Safe SQL Query Builders & Allow-List Sorting.

Provides pure helper functions to construct parameterized SQLAlchemy filter
expressions and enforce strict sort-order allow-lists (OWASP A03 Injection Prevention).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING

from fastapi import HTTPException
from sqlalchemy import BinaryExpression, ColumnElement, Select, or_

from app.schemas.filters import (
    DocumentFilterParams,
    FeedbackFilterParams,
    GovernanceFilterParams,
    QueryFilterParams,
    SessionFilterParams,
    UserFilterParams,
)

if TYPE_CHECKING:
    from app.models.db_models import (
        AuditLog,
        ChatMessageRecord,
        ChatSession,
        Document,
        MessageFeedback,
        User,
    )


def escape_like_expression(term: str) -> str:
    """Escape SQL LIKE/ILIKE wildcard characters (% and _) to prevent wildcard injection (OWASP A03)."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def build_date_range_filters(
    column: ColumnElement[datetime],
    start_date: datetime | None,
    end_date: datetime | None,
) -> list[BinaryExpression[bool]]:
    """Constructs parameterized start_date and end_date boundary expressions."""
    clauses: list[BinaryExpression[bool]] = []
    if start_date is not None:
        clauses.append(column >= start_date)
    if end_date is not None:
        clauses.append(column <= end_date)
    return clauses


def build_search_filter(
    columns: Sequence[ColumnElement[str]],
    search_term: str | None,
) -> ColumnElement[bool] | None:
    """Constructs an ILIKE OR-expression across specified text columns with wildcard escaping."""
    if not search_term or not search_term.strip():
        return None
    escaped = escape_like_expression(search_term.strip())
    pattern = f"%{escaped}%"
    return or_(*[col.ilike(pattern, escape="\\") for col in columns])


def apply_sort_order(
    query: Select,
    sort_key: str | None,
    allowed_sorts_map: dict[str, ColumnElement | Sequence[ColumnElement]],
    default_sort: ColumnElement | Sequence[ColumnElement] | None = None,
) -> Select:
    """Applies validated sort order from an explicit allow-list.

    Raises HTTP 422 Unprocessable Entity if an unlisted or malicious sort key
    is supplied (OWASP A03: Injection defense).
    """
    if not sort_key:
        if default_sort is not None:
            if isinstance(default_sort, (list, tuple)):
                return query.order_by(*default_sort)
            return query.order_by(default_sort)
        return query

    if sort_key not in allowed_sorts_map:
        allowed_keys = ", ".join(sorted(allowed_sorts_map.keys()))
        raise HTTPException(
            status_code=422,
            detail=f"Invalid sort key '{sort_key}'. Allowed sorts: {allowed_keys}",
        )

    order_expr = allowed_sorts_map[sort_key]
    if isinstance(order_expr, (list, tuple)):
        return query.order_by(*order_expr)
    return query.order_by(order_expr)


# ---------------------------------------------------------------------------
# Domain Filter Clause Builders
# ---------------------------------------------------------------------------


def build_user_filter_clauses(
    user_model: type[User],
    filters: UserFilterParams,
    test_emails: set[str] | None = None,
) -> list[BinaryExpression[bool] | ColumnElement[bool]]:
    """Builds parameterized WHERE clauses for the Users analytical table."""
    clauses: list[BinaryExpression[bool] | ColumnElement[bool]] = []

    # Date range on created_at
    clauses.extend(
        build_date_range_filters(
            user_model.created_at, filters.start_date, filters.end_date
        )
    )

    # Search filter across email and full_name
    search_expr = build_search_filter(
        [user_model.email, user_model.full_name], filters.q
    )
    if search_expr is not None:
        clauses.append(search_expr)

    # User Type (production vs test)
    if filters.user_type != "all" and test_emails:
        if filters.user_type == "test":
            clauses.append(user_model.email.in_(test_emails))
        elif filters.user_type == "production":
            clauses.append(user_model.email.notin_(test_emails))

    return clauses


def build_session_filter_clauses(
    session_model: type[ChatSession],
    filters: SessionFilterParams,
    test_ips: set[str] | None = None,
    audience: str = "overview",
) -> list[BinaryExpression[bool] | ColumnElement[bool]]:
    """Builds parameterized WHERE clauses for ChatSession records."""
    clauses: list[BinaryExpression[bool] | ColumnElement[bool]] = []

    # Date range on created_at
    clauses.extend(
        build_date_range_filters(
            session_model.created_at, filters.start_date, filters.end_date
        )
    )

    # Search filter across user_email and title
    search_expr = build_search_filter(
        [session_model.user_email, session_model.title], filters.q
    )
    if search_expr is not None:
        clauses.append(search_expr)

    # Specific IP address filter
    if filters.ip:
        clauses.append(session_model.created_ip == filters.ip)

    # Audience scope classification
    if test_ips:
        if audience == "users":
            clauses.append(
                or_(
                    session_model.created_ip.is_(None),
                    session_model.created_ip.notin_(test_ips),
                )
            )
        elif audience == "testing":
            clauses.append(session_model.created_ip.in_(test_ips))

    return clauses


def build_query_filter_clauses(
    msg_model: type[ChatMessageRecord],
    filters: QueryFilterParams,
    test_ips: set[str] | None = None,
    audience: str = "overview",
) -> list[BinaryExpression[bool] | ColumnElement[bool]]:
    """Builds parameterized WHERE clauses for ChatMessageRecord / Turn Economics table."""
    clauses: list[BinaryExpression[bool] | ColumnElement[bool]] = []

    # Only inspect assistant turns for token/economics, or both if needed
    # Date range on created_at
    clauses.extend(
        build_date_range_filters(
            msg_model.created_at, filters.start_date, filters.end_date
        )
    )

    # Search filter across content and outcome
    search_expr = build_search_filter([msg_model.content], filters.q)
    if search_expr is not None:
        clauses.append(search_expr)

    # Outcome filter
    if filters.outcome:
        clauses.append(msg_model.outcome == filters.outcome)

    # Status filter
    if filters.status == "error":
        clauses.append(msg_model.outcome == "error")
    elif filters.status == "timeout":
        clauses.append(msg_model.outcome == "timeout")
    elif filters.status == "success":
        clauses.append(
            or_(
                msg_model.outcome.is_(None),
                msg_model.outcome.notin_(["error", "timeout"]),
            )
        )

    # Cost range
    if filters.min_cost is not None:
        clauses.append(msg_model.cost_usd >= filters.min_cost)
    if filters.max_cost is not None:
        clauses.append(msg_model.cost_usd <= filters.max_cost)

    # Audience scope
    if test_ips:
        if audience == "users":
            clauses.append(
                or_(
                    msg_model.ip_address.is_(None),
                    msg_model.ip_address.notin_(test_ips),
                )
            )
        elif audience == "testing":
            clauses.append(msg_model.ip_address.in_(test_ips))

    return clauses


def build_feedback_filter_clauses(
    feedback_model: type[MessageFeedback],
    filters: FeedbackFilterParams,
) -> list[BinaryExpression[bool] | ColumnElement[bool]]:
    """Builds parameterized WHERE clauses for MessageFeedback records."""
    clauses: list[BinaryExpression[bool] | ColumnElement[bool]] = []

    # Date range on created_at
    clauses.extend(
        build_date_range_filters(
            feedback_model.created_at, filters.start_date, filters.end_date
        )
    )

    # Search filter across comment and actor
    search_expr = build_search_filter(
        [feedback_model.actor, feedback_model.comment], filters.q
    )
    if search_expr is not None:
        clauses.append(search_expr)

    # Rating filter
    if filters.rating != "all":
        if filters.rating in {"1", "2", "3", "4", "5"}:
            clauses.append(feedback_model.rating == int(filters.rating))
        elif filters.rating == "positive":
            clauses.append(feedback_model.rating >= 4)
        elif filters.rating == "negative":
            clauses.append(feedback_model.rating <= 2)

    return clauses


def build_governance_filter_clauses(
    audit_model: type[AuditLog],
    filters: GovernanceFilterParams,
) -> list[BinaryExpression[bool] | ColumnElement[bool]]:
    """Builds parameterized WHERE clauses for AuditLog / Governance records."""
    clauses: list[BinaryExpression[bool] | ColumnElement[bool]] = []

    # Date range on created_at
    clauses.extend(
        build_date_range_filters(
            audit_model.created_at, filters.start_date, filters.end_date
        )
    )

    # Search filter across actor, action, request_id
    search_expr = build_search_filter(
        [audit_model.actor, audit_model.action, audit_model.request_id], filters.q
    )
    if search_expr is not None:
        clauses.append(search_expr)

    # Action / service filter
    if filters.service:
        clauses.append(audit_model.action == filters.service)

    return clauses


def build_document_filter_clauses(
    doc_model: type[Document],
    filters: DocumentFilterParams,
) -> list[BinaryExpression[bool] | ColumnElement[bool]]:
    """Builds parameterized WHERE clauses for Knowledge Base Document records."""
    clauses: list[BinaryExpression[bool] | ColumnElement[bool]] = []

    # Date range on created_at
    clauses.extend(
        build_date_range_filters(
            doc_model.created_at, filters.start_date, filters.end_date
        )
    )

    # Search filter across source_name and source_type
    search_expr = build_search_filter(
        [doc_model.source_name, doc_model.source_type], filters.q
    )
    if search_expr is not None:
        clauses.append(search_expr)

    # Origin format
    if filters.origin_format and filters.origin_format != "all":
        clauses.append(doc_model.source_type == filters.origin_format.lower())

    return clauses
