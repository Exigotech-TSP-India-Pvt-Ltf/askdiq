"""Global exception handlers returning RFC 9457 problem+json (OWASP A10).

Every error body has ``type``, ``title``, ``status``, ``detail``,
``request_id`` and a stable machine-readable ``code`` (validation errors add
``errors``); no stack traces or internals are ever returned. ``detail``
keeps the existing ``HTTPException.detail`` value so current clients (which
read ``body.detail``) keep working. Logs carry the ``request_id`` and are
PII-masked through ``app.governance.pii``.

Infrastructure failures that escape a route are mapped here instead of
becoming opaque 500s:

- Postgres ``statement_timeout`` -> 503 ``db_timeout`` (+ ``Retry-After``)
- pool exhausted / connection lost -> 503 ``db_unavailable``
- Azure OpenAI 429 -> 503 ``upstream_rate_limited`` (+ ``Retry-After``)
- Azure OpenAI timeout -> 504 ``upstream_timeout``
- Azure OpenAI connection / 5xx / other API errors -> 503/502
- open LLM circuit breaker -> 503 ``upstream_circuit_open``
"""

from __future__ import annotations

import math
import traceback
import uuid
from http import HTTPStatus
from typing import Any

import openai
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from sqlalchemy.exc import DBAPIError, DisconnectionError, InterfaceError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.circuit import CircuitOpenError
from app.core.config import get_settings
from app.core.database import is_statement_timeout
from app.core.errors import ApiHTTPException, ErrorCode, code_for_status
from app.core.logging_config import get_logger
from app.core.security_headers import apply_security_headers, build_security_headers
from app.governance.audit import record_server_error
from app.governance.pii import redact_pii

logger = get_logger(__name__)

PROBLEM_JSON = "application/problem+json"
ABOUT_BLANK = "about:blank"
VALIDATION_TYPE = "urn:askdiq:problem:validation-error"
INTERNAL_TYPE = "urn:askdiq:problem:internal-error"
DEPENDENCY_TYPE = "urn:askdiq:problem:dependency-unavailable"
INTERNAL_DETAIL = (
    "An unexpected error occurred. Quote the request_id when contacting support."
)
VALIDATION_DETAIL = "Request validation failed."
DB_TIMEOUT_DETAIL = "The query took too long. Please retry or narrow the range."
DB_UNAVAILABLE_DETAIL = "The database is temporarily unavailable. Please retry shortly."
UPSTREAM_RATE_LIMITED_DETAIL = (
    "The AI service is receiving too many requests. Please retry shortly."
)
UPSTREAM_TIMEOUT_DETAIL = "The AI service did not respond in time. Please retry."
UPSTREAM_UNAVAILABLE_DETAIL = (
    "The AI service is temporarily unavailable. Please retry shortly."
)
UPSTREAM_ERROR_DETAIL = "The AI service returned an error. Please retry."
BODYLESS_STATUSES = frozenset({204, 304})
# Retry-After (seconds) for transient dependency failures without a hint.
DEFAULT_RETRY_AFTER_SECONDS = 5

__all__ = [
    "ApiHTTPException",
    "ConflictError",
    "DomainError",
    "ErrorCode",
    "ForbiddenError",
    "NotFoundError",
    "code_for_status",
    "problem_response",
    "register_exception_handlers",
]


class ProblemJSONResponse(JSONResponse):
    media_type = PROBLEM_JSON


def _mask(text: str) -> str:
    return redact_pii(text)[0]


def get_request_id(request: Request) -> str:
    """The id assigned by LatencyMiddleware, or a fresh one if it never ran."""
    request_id = getattr(request.state, "request_id", None)
    if not request_id:
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
    return request_id


def _title(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return "Error"


def problem_response(
    request: Request,
    status: int,
    detail: Any,
    *,
    problem_type: str = ABOUT_BLANK,
    headers: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
    code: str | None = None,
) -> ProblemJSONResponse:
    request_id = get_request_id(request)
    body: dict[str, Any] = {
        "type": problem_type,
        "title": _title(status),
        "status": status,
        "detail": detail,
        "code": code or code_for_status(status),
        "request_id": request_id,
    }
    if extra:
        # ``extra`` may add fields but never replace the envelope's own.
        body.update({k: v for k, v in extra.items() if k not in body})
    response = ProblemJSONResponse(body, status_code=status, headers=headers)
    response.headers["X-Request-ID"] = request_id
    return response


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> Response:
    if exc.status_code in BODYLESS_STATUSES:
        # 204/304 must not carry a body (e.g. ETag revalidation).
        return Response(
            status_code=exc.status_code, headers=getattr(exc, "headers", None)
        )
    if exc.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR:
        logger.error(
            "http_exception",
            request_id=get_request_id(request),
            path=request.url.path,
            status_code=exc.status_code,
            detail=_mask(str(exc.detail)),
        )
    detail = exc.detail if exc.detail is not None else _title(exc.status_code)
    return problem_response(
        request,
        exc.status_code,
        detail,
        headers=getattr(exc, "headers", None),
        code=getattr(exc, "code", None),
    )


def _field_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    """Field errors without ``input``/``ctx`` so submitted values are not echoed."""
    return [
        {
            "loc": [str(part) for part in err.get("loc", ())],
            "msg": _mask(str(err.get("msg", ""))),
            "type": str(err.get("type", "")),
        }
        for err in exc.errors()
    ]


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> ProblemJSONResponse:
    errors = _field_errors(exc)
    logger.info(
        "request_validation_failed",
        request_id=get_request_id(request),
        path=request.url.path,
        fields=[".".join(e["loc"]) for e in errors],
    )
    return problem_response(
        request,
        422,
        VALIDATION_DETAIL,
        problem_type=VALIDATION_TYPE,
        extra={"errors": errors},
        code=ErrorCode.VALIDATION_ERROR,
    )


async def unhandled_exception_handler(
    request: Request, exc: Exception
) -> ProblemJSONResponse:
    request_id = get_request_id(request)
    logger.error(
        "unhandled_exception",
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        error_type=type(exc).__name__,
        error=_mask(str(exc)),
        traceback=_mask("".join(traceback.format_exception(exc))),
    )
    # Unhandled exceptions bypass LatencyMiddleware's 5xx-response hook,
    # so this is the single place they are audited.
    record_server_error(request_id, request.url.path, 500, type(exc).__name__)
    response = problem_response(
        request,
        500,
        INTERNAL_DETAIL,
        problem_type=INTERNAL_TYPE,
        code=ErrorCode.INTERNAL_ERROR,
    )
    # Unhandled errors are answered by Starlette's outermost
    # ServerErrorMiddleware, outside the user middleware stack, so the
    # security headers are applied here directly.
    apply_security_headers(
        response.headers,
        build_security_headers(get_settings()),
        request.url.path,
    )
    return response


# --- Dependency failures (database, Azure OpenAI, circuit breaker) ---------


def _retry_after_header(seconds: float | None) -> dict[str, str]:
    value = DEFAULT_RETRY_AFTER_SECONDS if seconds is None else seconds
    return {"Retry-After": str(max(1, math.ceil(value)))}


def _upstream_retry_after(exc: openai.APIStatusError) -> float | None:
    """Seconds from the Azure ``Retry-After`` header, when it is a number."""
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    raw = headers.get("retry-after") if hasattr(headers, "get") else None
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def classify_dependency_error(
    exc: BaseException,
) -> tuple[int, str, str, dict[str, str] | None] | None:
    """``(status, code, detail, headers)`` for a known dependency failure.

    ``None`` means "not a dependency failure" (the generic 500 applies).
    """
    if isinstance(exc, CircuitOpenError):
        return (
            503,
            ErrorCode.UPSTREAM_CIRCUIT_OPEN,
            UPSTREAM_UNAVAILABLE_DETAIL,
            _retry_after_header(exc.retry_after),
        )
    if isinstance(exc, openai.RateLimitError):
        return (
            503,
            ErrorCode.UPSTREAM_RATE_LIMITED,
            UPSTREAM_RATE_LIMITED_DETAIL,
            _retry_after_header(_upstream_retry_after(exc)),
        )
    if isinstance(exc, openai.APITimeoutError):
        return 504, ErrorCode.UPSTREAM_TIMEOUT, UPSTREAM_TIMEOUT_DETAIL, None
    if isinstance(exc, openai.APIConnectionError):
        return (
            503,
            ErrorCode.UPSTREAM_UNAVAILABLE,
            UPSTREAM_UNAVAILABLE_DETAIL,
            _retry_after_header(None),
        )
    if isinstance(exc, openai.APIStatusError) and exc.status_code >= 500:  # noqa: PLR2004
        return (
            503,
            ErrorCode.UPSTREAM_UNAVAILABLE,
            UPSTREAM_UNAVAILABLE_DETAIL,
            _retry_after_header(None),
        )
    if isinstance(exc, openai.APIError):
        return 502, ErrorCode.UPSTREAM_ERROR, UPSTREAM_ERROR_DETAIL, None
    if is_statement_timeout(exc):
        return 503, ErrorCode.DB_TIMEOUT, DB_TIMEOUT_DETAIL, _retry_after_header(None)
    if isinstance(exc, PoolTimeoutError | DisconnectionError | InterfaceError) or (
        isinstance(exc, DBAPIError) and exc.connection_invalidated
    ):
        return (
            503,
            ErrorCode.DB_UNAVAILABLE,
            DB_UNAVAILABLE_DETAIL,
            _retry_after_header(None),
        )
    return None


async def dependency_exception_handler(request: Request, exc: Exception) -> Response:
    """Map database / Azure OpenAI failures to 503/504/502 problem+json.

    Runs inside the middleware stack (Starlette's ExceptionMiddleware), so
    LatencyMiddleware audits the 5xx response as one ``server_error`` row and
    adds the security headers. An unrecognised error is re-raised to the
    generic 500 handler.
    """
    mapped = classify_dependency_error(exc)
    if mapped is None:
        raise exc
    status, code, detail, headers = mapped
    request_id = get_request_id(request)
    logger.error(
        "dependency_error",
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        status_code=status,
        code=str(code),
        error_type=type(exc).__name__,
        error=_mask(str(exc))[:500],
    )
    return problem_response(
        request,
        status,
        detail,
        problem_type=DEPENDENCY_TYPE,
        headers=headers,
        code=code,
    )


class DomainError(Exception):
    """A service-layer failure that maps to an HTTP status.

    Services raise these instead of ``HTTPException`` so business logic stays
    free of the web framework; the handler below renders problem+json.
    ``detail`` must be safe to show to the caller.
    """

    status_code: int = 400
    code: str = ErrorCode.BAD_REQUEST

    def __init__(
        self,
        detail: str,
        *,
        extra: dict[str, Any] | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.extra = extra
        if code is not None:
            self.code = code


class NotFoundError(DomainError):
    status_code = 404
    code = ErrorCode.NOT_FOUND


class ConflictError(DomainError):
    status_code = 409
    code = ErrorCode.CONFLICT


class ForbiddenError(DomainError):
    status_code = 403
    code = ErrorCode.FORBIDDEN


async def domain_exception_handler(
    request: Request, exc: DomainError
) -> ProblemJSONResponse:
    return problem_response(
        request, exc.status_code, exc.detail, extra=exc.extra, code=exc.code
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, domain_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    for dependency_error in (
        CircuitOpenError,
        openai.APIError,
        DBAPIError,
        PoolTimeoutError,
        DisconnectionError,
    ):
        app.add_exception_handler(dependency_error, dependency_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
