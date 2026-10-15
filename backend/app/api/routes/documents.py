import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.logging_config import get_logger
from app.core.request_meta import get_client_ip
from app.governance.audit import record_audit_event
from app.governance.auth import get_current_actor
from app.models.db_models import Chunk, Document
from app.schemas.document import DocumentSummary

logger = get_logger(__name__)
router = APIRouter()


@router.get("/documents", response_model=list[DocumentSummary])
async def list_documents(
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(get_current_actor),
) -> list[DocumentSummary]:
    stmt = (
        select(Document, func.count(Chunk.id).label("chunk_count"))
        .join(Chunk, Chunk.document_id == Document.id, isouter=True)
        .group_by(Document.id)
        .order_by(Document.created_at.desc())
    )
    rows = (await db.execute(stmt)).all()

    result = []
    for doc, chunk_count in rows:
        meta = doc.doc_metadata or {}
        orig_file = meta.get("original_filename") or doc.source_name
        conv_from = meta.get("converted_from")
        if not conv_from:
            if (
                orig_file.lower().endswith(".docx")
                or meta.get("original_format") == "docx"
            ):
                conv_from = "docx"
            elif orig_file.lower().endswith((".md", ".markdown")) or meta.get(
                "original_format"
            ) in ("md", "markdown"):
                conv_from = "md"
            else:
                conv_from = None

        hash_val = doc.content_hash or meta.get("content_hash")
        short_hash = hash_val[:12] if hash_val else None

        result.append(
            DocumentSummary(
                document_id=str(doc.id),
                source_name=doc.source_name,
                source_type=doc.source_type,
                chunk_count=chunk_count,
                created_at=doc.created_at,
                updated_at=getattr(doc, "updated_at", None) or doc.created_at,
                original_format=meta.get("original_format")
                or conv_from
                or doc.source_type,
                active_format="MARKDOWN",
                converted_from=conv_from,
                original_filename=orig_file,
                content_hash=short_hash,
                original_sha256=meta.get("original_sha256") or doc.content_hash,
                ingest_count=meta.get("ingest_count", 1),
                warnings=meta.get("warnings", []),
                quarantined=meta.get("quarantined", False),
            )
        )
    return result


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(
    request: Request,
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(get_current_actor),
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
        actor=actor,
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
        actor=actor,
    )
    return Response(status_code=204)


@router.get("/documents/{document_id}/chunks")
async def list_document_chunks(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(get_current_actor),
) -> list[dict]:
    rows = (
        (
            await db.execute(
                select(Chunk)
                .where(Chunk.document_id == document_id)
                .order_by(Chunk.chunk_index)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(c.id),
            "chunk_index": c.chunk_index,
            "content": c.content,
            "strategy": c.strategy,
            "section_heading": (c.chunk_metadata or {}).get("section_heading"),
            "char_count": len(c.content),
        }
        for c in rows
    ]
