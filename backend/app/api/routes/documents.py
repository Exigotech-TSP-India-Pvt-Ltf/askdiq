import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.governance.auth import get_current_actor
from app.models.db_models import Chunk, Document
from app.schemas.document import DocumentSummary

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

    return [
        DocumentSummary(
            document_id=str(doc.id),
            source_name=doc.source_name,
            source_type=doc.source_type,
            chunk_count=chunk_count,
            created_at=doc.created_at,
        )
        for doc, chunk_count in rows
    ]


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(get_current_actor),
) -> Response:
    document = await db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    # Chunks are removed explicitly; async sessions can't lazy-load the ORM cascade.
    await db.execute(delete(Chunk).where(Chunk.document_id == document_id))
    await db.delete(document)
    await db.commit()
    return Response(status_code=204)

@router.get("/documents/{document_id}/chunks")
async def list_document_chunks(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(get_current_actor),
) -> list[dict]:
    rows = (
        await db.execute(
            select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.chunk_index)
        )
    ).scalars().all()
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