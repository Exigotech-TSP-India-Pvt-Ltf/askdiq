from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rate_limit import rate_limited_actor
from app.core.request_meta import get_client_ip
from app.core.sessions import (
    load_recent_history,
    record_exchange,
    resolve_session_for_query,
)
from app.core.config import get_settings
from app.governance.audit import record_audit_event
from app.harness.orchestrator import run_query
from app.retrieval.vector_store import VectorStore
from app.schemas.query import QueryRequest, QueryResponse

router = APIRouter()
settings = get_settings()


@router.post("/query", response_model=QueryResponse)
async def query(
    request: Request,
    body: QueryRequest,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(rate_limited_actor),
) -> QueryResponse:
    vector_store = VectorStore(db)
    # Client-supplied override (testing/demo) takes precedence; otherwise
    # fall back to the auto-detected real client IP.
    client_ip = body.ip_address or get_client_ip(request)

    session = await resolve_session_for_query(db, actor, body.session_id, client_ip)
    history = await load_recent_history(db, session)

    response = await run_query(
        query=body.query,
        top_k=settings.default_top_k,
        filters=body.filters,
        vector_store=vector_store,
        request_id=request.state.request_id,
        stage_timings=request.state.stage_timings,
        history=history,
    )

    user_message_id, assistant_message_id = await record_exchange(
        db,
        session=session,
        user_text=body.query,
        answer_text=response.answer,
        request_id=request.state.request_id,
        grounded=response.grounded,
        guardrail_flags=response.guardrail_flags,
        client_ip=client_ip,
    )

    await record_audit_event(
        db,
        request_id=request.state.request_id,
        actor=actor,
        action="query",
        detail={
            "query": body.query,
            "session_id": str(session.id),
            "grounded": response.grounded,
            "guardrail_flags": response.guardrail_flags,
            "source_ids": [s.chunk_id for s in response.sources],
        },
        ip_address=client_ip,
    )

    response.session_id = str(session.id)
    response.session_title = session.title
    response.user_message_id = user_message_id
    response.assistant_message_id = assistant_message_id
    response.ip_address = client_ip or ""
    return response
