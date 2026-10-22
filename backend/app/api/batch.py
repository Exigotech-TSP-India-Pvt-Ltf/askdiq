"""Batched detail reads.

A list page warms the details of its visible rows with ONE request
(``GET .../batch?ids=a&ids=b``), so a click on any row opens its drawer or
detail page from the browser cache. Each batch route reuses its single-item
reader (snapshot / query), so a batch item equals the single-item response.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from fastapi import HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field

from app.core.http_cache import cached_response

# Upper bound on ids per batch (the largest list page shows 25 rows).
BATCH_MAX_IDS = 25
# Longest id accepted (UUIDs, request ids and case ids are far shorter).
BATCH_ID_MAX_LENGTH = 128

BATCH_IDS_DESCRIPTION = (
    f"Ids to read (repeat the parameter; 1 to {BATCH_MAX_IDS}, duplicates ignored)."
)


class BatchDetails(BaseModel):
    """``items``: id -> the single-item response; ``missing``: unknown ids."""

    items: dict[str, Any] = Field(default_factory=dict)
    missing: list[str] = Field(default_factory=list)


BATCH_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {"model": BatchDetails},
    304: {"description": "ETag matched."},
    401: {"description": "Not authenticated."},
    403: {"description": "Missing `dashboard:read`."},
    422: {"description": f"No ids, more than {BATCH_MAX_IDS}, or an id too long."},
}


def batch_ids(
    ids: list[str] = Query(..., description=BATCH_IDS_DESCRIPTION),
) -> list[str]:
    """The distinct, non-empty ids in request order (422 when out of bounds)."""
    unique = list(dict.fromkeys(i.strip() for i in ids if i and i.strip()))
    if not unique or len(unique) > BATCH_MAX_IDS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Pass between 1 and {BATCH_MAX_IDS} ids.",
        )
    if any(len(i) > BATCH_ID_MAX_LENGTH for i in unique):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"An id is longer than {BATCH_ID_MAX_LENGTH} characters.",
        )
    return unique


async def collect(
    ids: Iterable[str], load: Callable[[str], Awaitable[Any | None]]
) -> BatchDetails:
    """``load`` each id in turn; ``None`` or a 404 marks it missing."""
    result = BatchDetails()
    for item_id in ids:
        try:
            value = await load(item_id)
        except HTTPException as exc:
            if exc.status_code != status.HTTP_404_NOT_FOUND:
                raise
            value = None
        if value is None:
            result.missing.append(item_id)
        else:
            result.items[item_id] = value
    return result


def batch_response(request: Request, details: BatchDetails) -> Response:
    """The batch as JSON with an ETag (304 on a matching revalidation)."""
    return cached_response(request, details.model_dump(mode="json"), max_age=0)
