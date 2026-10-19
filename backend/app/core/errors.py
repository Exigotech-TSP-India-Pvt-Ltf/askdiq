"""Stable error codes of the problem+json envelope (no app imports).

Kept dependency-free so low-level modules (``app.core.database``,
``app.auth.deps``) can raise coded errors without importing the exception
handlers (``app.core.exceptions``), which import them.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from fastapi import HTTPException


class ErrorCode(StrEnum):
    """Machine-readable ``code`` values; clients may switch on them."""

    BAD_REQUEST = "bad_request"
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    CONFLICT = "conflict"
    PRECONDITION_FAILED = "precondition_failed"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    VALIDATION_ERROR = "validation_error"
    RATE_LIMITED = "rate_limited"
    INTERNAL_ERROR = "internal_error"
    BAD_GATEWAY = "bad_gateway"
    SERVICE_UNAVAILABLE = "service_unavailable"
    GATEWAY_TIMEOUT = "gateway_timeout"
    HTTP_ERROR = "http_error"
    # Specific codes.
    INVALID_SERVICE_KEY = "invalid_service_key"
    CSRF_REJECTED = "csrf_rejected"
    ACCOUNT_SUSPENDED = "account_suspended"
    APPROVAL_REQUIRED = "approval_required"
    INSUFFICIENT_PERMISSIONS = "insufficient_permissions"
    DB_TIMEOUT = "db_timeout"
    DB_UNAVAILABLE = "db_unavailable"
    UPSTREAM_RATE_LIMITED = "upstream_rate_limited"
    UPSTREAM_TIMEOUT = "upstream_timeout"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"
    UPSTREAM_ERROR = "upstream_error"
    UPSTREAM_CIRCUIT_OPEN = "upstream_circuit_open"


_CODE_BY_STATUS: dict[int, ErrorCode] = {
    400: ErrorCode.BAD_REQUEST,
    401: ErrorCode.UNAUTHENTICATED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED,
    409: ErrorCode.CONFLICT,
    412: ErrorCode.PRECONDITION_FAILED,
    413: ErrorCode.PAYLOAD_TOO_LARGE,
    415: ErrorCode.UNSUPPORTED_MEDIA_TYPE,
    422: ErrorCode.VALIDATION_ERROR,
    429: ErrorCode.RATE_LIMITED,
    500: ErrorCode.INTERNAL_ERROR,
    502: ErrorCode.BAD_GATEWAY,
    503: ErrorCode.SERVICE_UNAVAILABLE,
    504: ErrorCode.GATEWAY_TIMEOUT,
}


def code_for_status(status: int) -> str:
    """Default ``code`` for an HTTP status (``http_error`` when unmapped)."""
    return str(_CODE_BY_STATUS.get(status, ErrorCode.HTTP_ERROR))


class ApiHTTPException(HTTPException):
    """``HTTPException`` carrying a specific error ``code`` for the envelope."""

    def __init__(
        self,
        status_code: int,
        detail: Any = None,
        *,
        code: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code, detail=detail, headers=headers)
        self.code = str(code)
