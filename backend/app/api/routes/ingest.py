from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

import asyncio

from app.chunking.factory import get_strategy
from app.core.database import get_db
from app.governance.auth import get_current_actor
from app.governance.pii import redact_pii
from app.models.db_models import Chunk, Document
from app.retrieval.embeddings import embed_texts_sync
from app.schemas.document import IngestRequest, IngestResponse

router = APIRouter()


@router.post("/ingest", response_model=IngestResponse)
async def ingest(
    body: IngestRequest,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(get_current_actor),
) -> IngestResponse:
    redacted_content, pii_found = redact_pii(body.content)

    document = Document(
        source_name=body.source_name,
        source_type=body.source_type,
        doc_metadata={**body.metadata, "pii_redacted": pii_found},
    )
    db.add(document)
    await db.flush()  # get document.id before creating chunks

    strategy = get_strategy(body.chunking_strategy, source_type=body.source_type)
    chunk_results = strategy.chunk(redacted_content, source_metadata={"document_id": str(document.id)})

    if chunk_results:
        # Batch-embed all chunks in one local call (RETRIEVAL_DOCUMENT task type)
        # rather than embedding one chunk at a time.
        embeddings = await asyncio.to_thread(
            embed_texts_sync, [cr.content for cr in chunk_results], "RETRIEVAL_DOCUMENT"
        )
    else:
        embeddings = []

    for cr, embedding in zip(chunk_results, embeddings):
        db.add(
            Chunk(
                document_id=document.id,
                content=cr.content,
                embedding=embedding,
                strategy=cr.metadata.get("strategy", body.chunking_strategy),
                chunk_index=cr.chunk_index,
                chunk_metadata=cr.metadata,
            )
        )

    await db.commit()

    return IngestResponse(
        document_id=str(document.id),
        chunk_count=len(chunk_results),
        strategy_used=chunk_results[0].metadata.get("strategy") if chunk_results else body.chunking_strategy,
    )
