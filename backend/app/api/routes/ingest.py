"""Document ingestion endpoint (incremental: only new/changed chunks are embedded)."""

import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.evaluation.kb_eval import KBChunk, evaluate_kb
from app.governance.auth import get_current_actor
from app.governance.pii import redact_pii
from app.ingestion.service import ingest_text
from app.schemas.document import IngestRequest, IngestResponse

router = APIRouter()


@router.post("/ingest", response_model=IngestResponse)
async def ingest(
    body: IngestRequest,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(get_current_actor),
) -> IngestResponse:
    redacted_content, pii_found = redact_pii(body.content)

    try:
        result = await ingest_text(
            db,
            source_name=body.source_name,
            source_type=body.source_type,
            content=redacted_content,
            chunking_strategy=body.chunking_strategy,
            metadata={**body.metadata, "pii_redacted": pii_found},
        )
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {exc}") from exc

    document = result["document"]
    chunk_results = result["chunk_results"]
    stored = result["added"] + result["kept"]

    quality = None
    if chunk_results:
        try:
            kb_chunks = [
                KBChunk(body.source_name, cr.chunk_index, cr.content, dict(cr.metadata))
                for cr in chunk_results
            ]
            quality = evaluate_kb(
                {body.source_name: redacted_content},
                {body.source_name: kb_chunks},
                expected_docs=[body.source_name],
                timings_ms={body.source_name: result["chunk_ms"]},
            )
            quality["pii_redacted"] = pii_found
        except Exception:
            quality = None
    if quality is not None:
        quality["timings_ms"] = {
            "chunking": round(result["chunk_ms"], 1),
            "embedding": round(result["embed_ms"], 1),
            "total": round((time.perf_counter() - result["started"]) * 1000, 1),
        }

    return IngestResponse(
        document_id=str(document.id),
        chunk_count=stored,
        strategy_used=(
            chunk_results[0].metadata.get("strategy") if chunk_results else body.chunking_strategy
        ),
        status=result["status"],
        chunks_added=result["added"],
        chunks_kept=result["kept"],
        chunks_removed=result["removed"],
        quality=quality,
    )
