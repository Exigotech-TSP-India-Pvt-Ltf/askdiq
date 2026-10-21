from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.concurrency import llm_capacity
from app.core.config import get_settings
from app.core.database import get_db
from app.core.rate_limit import aenforce_ip_rate_limit, rate_limited_actor
from app.core.request_meta import resolve_client_ip
from app.core.request_meta import valid_ip as _valid_ip  # noqa: F401  (re-export)
from app.core.sessions import (
    exchange_write_task,
    load_recent_history,
    resolve_session_for_query,
    schedule_exchange_write,
)
from app.governance.costing import compute_exchange_cost
from app.governance.online_eval import schedule_online_evaluation
from app.governance.pii import redact_pii
from app.harness.clarify import CLARIFY_OUTCOME, resolve_clarification_reply
from app.harness.orchestrator import run_query
from app.retrieval.embeddings import embedding_tokens_sink
from app.retrieval.vector_store import VectorStore
from app.schemas.query import QueryRequest, QueryResponse

router = APIRouter()
settings = get_settings()


@router.post(
    "/query",
    response_model=QueryResponse,
    dependencies=[Depends(llm_capacity)],
    responses={429: {"description": "Rate limited or LLM capacity saturated"}},
)
async def query(
    request: Request,
    body: QueryRequest,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(rate_limited_actor),
) -> QueryResponse:
    # Subtask 2.1.3: Initialize embedding tokens sink at start of query route
    embed_sink: list[tuple[str, int]] = []
    embedding_tokens_sink.set(embed_sink)

    vector_store = VectorStore(db)
    # The website frontend calls server-to-server and passes each end user's IP
    # in the body; it is stored against that user and used for the per-IP limit.
    client_ip = resolve_client_ip(request, body.ip_address)
    await aenforce_ip_rate_limit(client_ip)

    session = await resolve_session_for_query(
        db, actor, body.session_id, client_ip, new_session=body.new_session
    )
    history = await load_recent_history(db, session)
    # Release the pooled connection before embedding/rerank/LLM.
    await db.commit()

    # A reply to a clarifying question (picked option, its number/text, or a
    # short refinement) is answered as the original question + that meaning.
    resolved = resolve_clarification_reply(
        body.query, history, body.clarification_answers
    )

    response = await run_query(
        query=resolved.query if resolved else body.query,
        boost_document_ids=resolved.document_ids if resolved else None,
        clarification_reply=resolved is not None,
        top_k=settings.default_top_k,
        filters=body.filters,
        vector_store=vector_store,
        request_id=request.state.request_id,
        stage_timings=request.state.stage_timings,
        history=history,
    )

    # Subtask 2.1.3 & 2.3.2: Separate query embedding tokens and guardrail embedding tokens
    query_embed_tokens = sum(tok for stage, tok in embed_sink if stage == "query")
    guardrail_embed_tokens = sum(
        tok for stage, tok in embed_sink if stage == "guardrail"
    )

    # Subtask 2.4.3: Compute exchange cost & snapshot pricing
    cost_usd, cost_details = compute_exchange_cost(
        prompt_tokens=response.prompt_tokens or 0,
        completion_tokens=response.completion_tokens or 0,
        reasoning_tokens=response.reasoning_tokens or 0,
        cached_tokens=(response.token_details or {}).get("cached_tokens", 0),
        query_embed_tokens=query_embed_tokens,
        guardrail_embed_tokens=guardrail_embed_tokens,
    )

    merged_details = {
        **cost_details,
        **(response.token_details or {}),
        "outcome": response.outcome,
    }
    if resolved:
        merged_details["clarification_answers"] = resolved.as_details()

    redacted_query, _ = redact_pii(body.query)

    user_message_id, assistant_message_id, session_title = schedule_exchange_write(
        user_email=actor,
        session=session,
        user_text=body.query,
        answer_text=response.answer,
        request_id=request.state.request_id,
        grounded=response.grounded,
        guardrail_flags=response.guardrail_flags,
        client_ip=client_ip,
        prompt_tokens=response.prompt_tokens,
        completion_tokens=response.completion_tokens,
        total_tokens=response.total_tokens,
        reasoning_tokens=response.reasoning_tokens,
        embedding_tokens=query_embed_tokens,
        guardrail_tokens=guardrail_embed_tokens,
        cost_usd=cost_usd,
        token_details=merged_details,
        outcome=response.outcome,
        audit_detail={
            "query": redacted_query,
            "session_id": str(session.id),
            "grounded": response.grounded,
            "guardrail_flags": response.guardrail_flags,
            "source_ids": [s.chunk_id for s in response.sources],
            # Retrieval scores for the governance trace drawer (ids, no content).
            "sources": [
                {
                    "chunk_id": s.chunk_id,
                    "document_id": s.document_id,
                    "score": round(float(s.score), 4),
                }
                for s in response.sources
            ],
            "cost_usd": cost_usd,
            "outcome": response.outcome,
            "tokens": merged_details.get("tokens", {}),
        },
    )

    # A clarifying question is not an answer, so there is nothing to judge.
    if response.outcome != CLARIFY_OUTCOME:
        schedule_online_evaluation(
            request_id=request.state.request_id,
            query=resolved.query if resolved else body.query,
            answer=response.answer,
            sources=response.sources,
            guardrail_flags=response.guardrail_flags,
            ip_address=client_ip,
            session_id=str(session.id),
            message_id=str(assistant_message_id) if assistant_message_id else None,
            outcome=response.outcome,
            grounded=response.grounded,
            confidence=response.confidence,
            latency_ms=response.latency_ms,
            after=exchange_write_task(assistant_message_id),
        )

    response.session_id = str(session.id)
    response.session_title = session_title
    response.user_message_id = user_message_id
    response.assistant_message_id = assistant_message_id
    response.ip_address = client_ip or ""
    response.embedding_tokens = query_embed_tokens
    response.guardrail_tokens = guardrail_embed_tokens
    response.cost_usd = cost_usd
    response.token_details = merged_details
    return response
