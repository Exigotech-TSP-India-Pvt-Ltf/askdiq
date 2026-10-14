from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.retrieval.reranker import is_reranker_loaded

router = APIRouter()


@router.get("/health")
async def health(
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Readiness probe for load balancers and orchestrators (Subtask 6.5.3)."""
    db_ok = False
    try:
        await db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    reranker_ok = is_reranker_loaded()

    if not db_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "degraded",
            "database": "unreachable",
            "reranker": "ready" if reranker_ok else "not_loaded",
            "timestamp": datetime.now(UTC).isoformat(),
        }

    return {
        "status": "ok",
        "database": "healthy",
        "reranker": "ready" if reranker_ok else "lazy_load",
        "timestamp": datetime.now(UTC).isoformat(),
    }
