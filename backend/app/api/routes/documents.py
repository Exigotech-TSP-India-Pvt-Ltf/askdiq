from fastapi import APIRouter, Depends
from sqlalchemy import func, select
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
