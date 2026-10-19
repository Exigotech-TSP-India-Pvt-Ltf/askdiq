"""Knowledge-base documents: inventory, detail, canonical Markdown, chunks, citations.

Reads need ``dashboard:read``; deletion needs ``documents:write``. Uploads are
in ``ingest.py``.
"""

import uuid
from datetime import date, timedelta
from enum import StrEnum
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_permission
from app.auth.permissions import Permission
from app.auth.principal import AuthUser
from app.core.concurrency import ScopedRunner, get_scoped_runner
from app.core.config import get_settings
from app.core.database import get_db
from app.core.http_cache import cached_response
from app.core.logging_config import get_logger
from app.core.request_meta import get_client_ip
from app.dashboard.snapshots import Gather, get_snapshot, register
from app.documents import citations, inventory
from app.governance.audit import record_audit_event
from app.models.db_models import Chunk, Document
from app.schemas.document import (
    CanonicalMarkdown,
    DocumentChunkItem,
    DocumentCitationStats,
    DocumentDetail,
    DocumentSummary,
    LowRatedCitations,
)

logger = get_logger(__name__)
router = APIRouter()

MAX_SEARCH_CHARS = 200
DEFAULT_RANGE_DAYS = 7
DEFAULT_TOP_CITED = 10
MAX_TOP_CITED = 50
DEFAULT_LOW_RATED = 20
MAX_LOW_RATED = 100

_AUTH_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Not signed in or missing service key"},
    403: {"description": "Insufficient permissions"},
}
_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"description": "Document not found"}
}
_INVALID: dict[int | str, dict[str, Any]] = {422: {"description": "Invalid parameter"}}


class ChunkSort(StrEnum):
    INDEX = "index"
    HITS = "hits"


def resolve_range(
    start: date | None, end: date | None, today: date
) -> tuple[date, date]:
    """Inclusive ``from``/``to`` days; default the last 7 days ending today."""
    end = end or today
    start = start or end - timedelta(days=DEFAULT_RANGE_DAYS - 1)
    if end < start:
        raise HTTPException(status_code=422, detail="'to' must not be before 'from'.")
    max_days = get_settings().usage_max_range_days
    if (end - start).days > max_days:
        raise HTTPException(
            status_code=422,
            detail=f"Date range exceeds the maximum of {max_days} days.",
        )
    return start, end


async def _require_document(db: AsyncSession, document_id: uuid.UUID) -> None:
    if not await inventory.document_exists(db, document_id):
        raise HTTPException(status_code=404, detail="Document not found")


@router.get(
    "/documents",
    response_model=list[DocumentSummary],
    summary="Document inventory with full ingestion metadata",
    responses=_AUTH_ERRORS,
)
async def list_documents(
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_permission(Permission.DASHBOARD_READ)),
) -> list[DocumentSummary]:
    """Every canonical document, newest first: formats (original and
    canonical), standardization, sizes, hashes, chunk counts and strategies,
    embedding model, ingestion time/actor, status and warnings. Fields a
    document never recorded are ``null``."""
    return await inventory.list_documents(db)


@router.get(
    "/documents/stats/citations",
    response_model=DocumentCitationStats,
    summary="Most cited documents, age vs citations and answer coverage",
    responses={**_AUTH_ERRORS, **_INVALID, 304: {"description": "ETag matched"}},
)
async def get_citation_stats(
    request: Request,
    from_: date | None = Query(None, alias="from", description="First day (UTC)"),
    to: date | None = Query(None, description="Last day, inclusive (UTC)"),
    limit: int = Query(DEFAULT_TOP_CITED, ge=1, le=MAX_TOP_CITED),
    runner: ScopedRunner = Depends(get_scoped_runner),
    user: AuthUser = Depends(require_permission(Permission.DASHBOARD_READ)),
) -> Response:
    """A citation is one assistant turn that listed the document among its
    sources (once per turn per document)."""
    start, end = resolve_range(from_, to, citations.utc_today())
    payload = await get_snapshot(
        CITATION_STATS_KIND,
        {"start": start.isoformat(), "end": end.isoformat(), "limit": limit},
        runner.gather,
    )
    return cached_response(request, payload)


CITATION_STATS_KIND = "documents.citations"


async def _citation_stats_snapshot(gather: Gather, params: dict) -> Any:
    start, end = date.fromisoformat(params["start"]), date.fromisoformat(params["end"])

    async def stats(session: AsyncSession) -> Any:
        return await citations.citation_stats(
            session, start=start, end=end, limit=params["limit"]
        )

    (payload,) = await gather(stats)
    # Stored in its wire form ("from" / "to"), as the route sends it.
    return payload.model_dump(mode="json", by_alias=True)


def _citation_defaults() -> list[dict]:
    """The Knowledge page's default range (as resolve_range picks it)."""
    start, end = resolve_range(None, None, citations.utc_today())
    return [
        {"start": start.isoformat(), "end": end.isoformat(), "limit": DEFAULT_TOP_CITED}
    ]


register(CITATION_STATS_KIND, _citation_stats_snapshot, _citation_defaults)


@router.get(
    "/documents/{document_id}",
    response_model=DocumentDetail,
    summary="One document's metadata, ingest history and chunk size stats",
    responses={**_AUTH_ERRORS, **_NOT_FOUND},
)
async def get_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_permission(Permission.DASHBOARD_READ)),
) -> DocumentDetail:
    detail = await inventory.get_document_detail(db, document_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return detail


@router.get(
    "/documents/{document_id}/canonical",
    response_model=CanonicalMarkdown,
    summary="The standardized Markdown the document was chunked from",
    responses={**_AUTH_ERRORS, **_NOT_FOUND},
)
async def get_canonical_markdown(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_permission(Permission.DASHBOARD_READ)),
) -> CanonicalMarkdown:
    """``available`` is false for documents ingested before the canonical text
    was stored; uploading the original again stores it."""
    canonical = await inventory.get_canonical_markdown(db, document_id)
    if canonical is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return canonical


@router.delete(
    "/documents/{document_id}",
    status_code=204,
    summary="Delete a document and all of its chunks (audited)",
    responses={**_AUTH_ERRORS, **_NOT_FOUND},
)
async def delete_document(
    request: Request,
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_permission(Permission.DOCUMENTS_WRITE)),
) -> Response:
    """Audited document and chunk deletion in a single atomic transaction."""
    document = await db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")

    source_name = document.source_name
    chunk_count = (
        await db.scalar(
            select(func.count(Chunk.id)).where(Chunk.document_id == document_id)
        )
        or 0
    )

    # Chunks and document removed in one atomic transaction
    await db.execute(delete(Chunk).where(Chunk.document_id == document_id))
    await db.delete(document)

    req_id = getattr(request.state, "request_id", f"del-{uuid.uuid4().hex[:12]}")
    client_ip = get_client_ip(request)

    # Record tamper-evident audit event inside the same transaction
    await record_audit_event(
        db,
        request_id=req_id,
        actor=user.email,
        action="document_delete",
        detail={
            "document_id": str(document_id),
            "source_name": source_name,
            "chunks_removed": chunk_count,
        },
        ip_address=client_ip,
        commit=False,
    )

    await db.commit()
    logger.info(
        "document_deleted",
        document_id=str(document_id),
        source_name=source_name,
        chunks_removed=chunk_count,
        actor=user.email,
    )
    return Response(status_code=204)


@router.get(
    "/documents/{document_id}/chunks",
    response_model=list[DocumentChunkItem],
    summary="Every chunk of a document with citation hits",
    responses={**_AUTH_ERRORS, **_NOT_FOUND, **_INVALID},
)
async def list_document_chunks(
    document_id: uuid.UUID,
    q: str | None = Query(
        None,
        max_length=MAX_SEARCH_CHARS,
        description="Case-insensitive match on chunk text or section heading",
    ),
    sort: ChunkSort = Query(ChunkSort.INDEX),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_permission(Permission.DASHBOARD_READ)),
) -> list[DocumentChunkItem]:
    """``hits`` = citing turns in the last 8 ISO weeks, ``low_rated_hits`` = of
    those, turns rated 2 or lower; ``weekly_hits`` omits zero weeks."""
    await _require_document(db, document_id)
    return await citations.list_chunks(
        db,
        document_id,
        q=(q or "").strip() or None,
        sort=sort.value,
        today=citations.utc_today(),
    )


@router.get(
    "/documents/{document_id}/low-rated-citations",
    response_model=LowRatedCitations,
    summary="Low-rated answers (rating 2 or lower) that cited this document",
    responses={**_AUTH_ERRORS, **_NOT_FOUND, **_INVALID},
)
async def list_low_rated_citations(
    document_id: uuid.UUID,
    limit: int = Query(DEFAULT_LOW_RATED, ge=1, le=MAX_LOW_RATED),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_permission(Permission.DASHBOARD_READ)),
) -> LowRatedCitations:
    """Newest first, one row per (assistant message, chunk); ``message_id``
    opens the Feedback queue."""
    await _require_document(db, document_id)
    return await citations.low_rated_citations(
        db, document_id, limit=limit, today=citations.utc_today()
    )
