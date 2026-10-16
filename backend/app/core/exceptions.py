"""Global exception handlers returning RFC 9457 problem+json (OWASP A10).

Every error body has ``type``, ``title``, ``status``, ``detail`` and
``request_id``; no stack traces or internals are ever returned. ``detail``
keeps the existing ``HTTPException.detail`` value so current clients (which
read ``body.detail``) keep working. Logs carry the ``request_id`` and are
PII-masked through ``app.governance.pii``.
"""

from __future__ import annotations

import traceback
import uuid
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.core.security_headers import apply_security_headers, build_security_headers
from app.governance.pii import redact_pii

logger = get_logger(__name__)

PROBLEM_JSON = "application/problem+json"
ABOUT_BLANK = "about:blank"
VALIDATION_TYPE = "urn:askdiq:problem:validation-error"
INTERNAL_TYPE = "urn:askdiq:problem:internal-error"
INTERNAL_DETAIL = (
    "An unexpected error occurred. Quote the request_id when contacting support."
)
VALIDATION_DETAIL = "Request validation failed."
BODYLESS_STATUSES = frozenset({204, 304})


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
) -> ProblemJSONResponse:
    request_id = get_request_id(request)
    body: dict[str, Any] = {
        "type": problem_type,
        "title": _title(status),
        "status": status,
        "detail": detail,
        "request_id": request_id,
    }
    if extra:
        body.update(extra)
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
    response = problem_response(
        request, 500, INTERNAL_DETAIL, problem_type=INTERNAL_TYPE
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


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
