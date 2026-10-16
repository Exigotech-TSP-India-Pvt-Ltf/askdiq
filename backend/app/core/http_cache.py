"""Conditional GET helpers: weak ETag, 304 Not Modified and Cache-Control."""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.core.config import get_settings

PRIVATE_VARY = "Cookie, X-Session, X-API-Key, X-Client-Id"


def weak_etag(body: bytes) -> str:
    return f'W/"{hashlib.sha256(body).hexdigest()[:32]}"'


def _etag_matches(if_none_match: str | None, etag: str) -> bool:
    if not if_none_match:
        return False
    if if_none_match.strip() == "*":
        return True
    opaque = etag.removeprefix("W/")
    candidates = (tag.strip().removeprefix("W/") for tag in if_none_match.split(","))
    return any(candidate == opaque for candidate in candidates)


def cached_response(
    request: Request,
    payload: Any,
    max_age: int | None = None,
    *,
    public: bool = False,
) -> Response:
    """JSON response with a weak ETag; 304 when ``If-None-Match`` matches.

    ``Cache-Control: private`` (the default) keeps per-user admin data out of
    shared caches; ``public=True`` is only for data that is the same for
    every caller (for example ``GET /branding``).
    """
    if max_age is None:
        max_age = get_settings().http_cache_max_age_seconds
    response = JSONResponse(jsonable_encoder(payload))
    etag = weak_etag(bytes(response.body))
    headers = {"ETag": etag}
    if public:
        headers["Cache-Control"] = f"public, max-age={max_age}"
    else:
        headers["Cache-Control"] = f"private, max-age={max_age}"
        # Per-user data: key the private cache on every credential carrier
        # (session cookie / X-Session header, API key, legacy client id).
        headers["Vary"] = PRIVATE_VARY
    if _etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    response.headers.update(headers)
    return response
