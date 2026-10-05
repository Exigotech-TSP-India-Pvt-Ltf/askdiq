"""The harness: structured orchestration around the model.

Pipeline:
retrieve -> pre-guardrails -> retrieval relevance check ->
build prompt -> call LLM (with retry) ->
validate output -> post-guardrails -> structured response.
"""

import asyncio
import logging
import time

from app.core.config import get_settings
from app.guardrails.conversational import detect_conversational_intent
from app.guardrails.post_checks import run_post_checks
from app.guardrails.pre_checks import run_pre_checks
from app.harness.prompts import _MAX_HISTORY_TURNS, call_llm
from app.retrieval.retriever import needs_conversation_context, retrieve
from app.retrieval.vector_store import VectorStore
from app.schemas.query import ChatTurn, QueryResponse, SourceChunk

logger = logging.getLogger(__name__)

settings = get_settings()


class GuardrailBlocked(Exception):
    """Raised when a guardrail blocks the request."""

    def __init__(self, flags: list[str]):
        self.flags = flags


def _fallback_response(
    request_id: str,
    flags: list[str],
    latency_ms: float,
    sources: list[SourceChunk] | None = None,
    follow_up_detected: bool = False,
    history_turns_sent: int = 0,
) -> QueryResponse:
    """Return a safe fallback response.

    Spam/unsafe input is blocked with a terse, dedicated message and never
    carries retrieval evidence — there's nothing useful (or safe) to expose
    for junk input. Genuine off-topic/low-confidence cases still surface
    whatever sources were considered, for transparency.
    """

    if "spam_detected" in flags:
        answer = "That doesn't look like a genuine question, so I can't help with it."
    elif "unsafe_input_detected" in flags:
        answer = "I can't process that request."
    else:
        answer = (
            "I can\u2019t find this information in the DeployIQ knowledge base. "
            "I can help with questions about the DeployIQ platform, Zero Trust, "
            "Essential Eight, Responsible AI, governance, and data handling."
        )

    return QueryResponse(
        answer=answer,
        sources=sources or [],
        grounded=False,
        confidence=0.0,
        request_id=request_id,
        latency_ms=latency_ms,
        guardrail_flags=flags,
        follow_up_detected=follow_up_detected,
        history_turns_sent=history_turns_sent,
    )


async def run_query(
    query: str,
    top_k: int,
    filters: dict | None,
    vector_store: VectorStore,
    request_id: str,
    stage_timings: dict,
    history: list[ChatTurn] | None = None,
) -> QueryResponse:
    """Run the complete RAG query pipeline.

    Flow:

    1. Pre-query guardrails
    2. Vector retrieval
    3. Retrieval relevance check
    4. LLM generation
    5. Post-generation groundedness checks
    6. Structured response
    """

    start = time.perf_counter()

    # Computed once up front (pure function of query+history) so every
    # response path — fallback or final — reports the same, consistent
    # values instead of recomputing (and potentially diverging).
    follow_up_detected = needs_conversation_context(query)
    history_turns_sent = (
        min(len(history or []), _MAX_HISTORY_TURNS) if follow_up_detected else 0
    )

    # ---------------------------------------------------------
    # 0. CONVERSATIONAL SMALL-TALK (greetings/thanks/capability asks)
    # ---------------------------------------------------------
    # Handled before guardrails/retrieval — these aren't real questions
    # about DeployIQ content, so they'd otherwise just get rejected as
    # off-topic by the retrieval relevance check.

    conversational_reply = detect_conversational_intent(query)

    if conversational_reply is not None:
        return QueryResponse(
            answer=conversational_reply,
            sources=[],
            grounded=True,
            confidence=1.0,
            request_id=request_id,
            latency_ms=(time.perf_counter() - start) * 1000,
            guardrail_flags=[],
            follow_up_detected=follow_up_detected,
            history_turns_sent=history_turns_sent,
        )

    # ---------------------------------------------------------
    # 1. PRE-GUARDRAILS
    # ---------------------------------------------------------

    t0 = time.perf_counter()

    pre_flags = run_pre_checks(
        query,
        settings.max_query_length,
    )

    stage_timings["guardrail_pre"] = (time.perf_counter() - t0) * 1000

    if pre_flags:
        return _fallback_response(
            request_id=request_id,
            flags=pre_flags,
            latency_ms=(time.perf_counter() - start) * 1000,
            follow_up_detected=follow_up_detected,
            history_turns_sent=history_turns_sent,
        )

    # ---------------------------------------------------------
    # 2. VECTOR RETRIEVAL
    # ---------------------------------------------------------

    t0 = time.perf_counter()

    sources: list[SourceChunk] = await retrieve(
        vector_store,
        query,
        top_k=top_k,
        filters=filters,
        history=history,
    )

    stage_timings["retrieval"] = (time.perf_counter() - t0) * 1000

    # ---------------------------------------------------------
    # 3. RETRIEVAL RELEVANCE CHECK
    # ---------------------------------------------------------

    if not sources:
        return _fallback_response(
            request_id=request_id,
            flags=["off_topic"],
            latency_ms=(time.perf_counter() - start) * 1000,
            follow_up_detected=follow_up_detected,
            history_turns_sent=history_turns_sent,
        )

    max_retrieval_score = max(source.score for source in sources)

    stage_timings["retrieval_relevance_score"] = max_retrieval_score

    if max_retrieval_score < settings.min_retrieval_score:
        return _fallback_response(
            request_id=request_id,
            flags=["off_topic"],
            latency_ms=(time.perf_counter() - start) * 1000,
            sources=sources,
            follow_up_detected=follow_up_detected,
            history_turns_sent=history_turns_sent,
        )

    logger.info(
        "retrieval request_id=%s query=%r sources=%d max_score=%.4f documents=%s",
        request_id,
        query,
        len(sources),
        max_retrieval_score,
        sorted({s.document_id for s in sources}),
    )
    logger.debug(
        "retrieval_detail request_id=%s sources=%s",
        request_id,
        [
            {
                "chunk_id": s.chunk_id,
                "score": s.score,
                "heading": (s.metadata or {}).get("section_heading"),
                "content": s.content,
            }
            for s in sources
        ],
    )

    # ---------------------------------------------------------
    # 4. LLM GENERATION (+ POST-GENERATION GROUNDEDNESS CHECK)
    # ---------------------------------------------------------
    #
    # gpt-5-mini must run at temperature=1.0 (its only supported value), so
    # phrasing — and therefore the semantic groundedness score — varies
    # sample to sample for the exact same question. Retrying generation on a
    # failed groundedness check (not just on empty/error) lets a fresh
    # sample succeed instead of surfacing the fallback on one unlucky draw.
    # ---------------------------------------------------------

    t0 = time.perf_counter()

    answer = ""
    last_error: Exception | None = None
    grounded = False
    confidence = 0.0
    post_flags: list[str] = []
    guardrail_post_ms = 0.0

    for attempt in range(settings.max_llm_retries + 1):
        try:
            answer = await asyncio.to_thread(
                call_llm,
                query,
                [source.content for source in sources],
                history,
            )
        except Exception as exc:
            last_error = exc
            answer = ""

            logger.error(
                "llm_call_failed request_id=%s attempt=%d error_type=%s error=%s",
                request_id,
                attempt + 1,
                type(exc).__name__,
                exc,
            )
            continue

        if not answer.strip():
            continue

        t_post = time.perf_counter()
        # Blocking Azure embedding call inside run_post_checks — off the
        # event loop so it doesn't stall every other concurrent request.
        grounded, confidence, post_flags = await asyncio.to_thread(
            run_post_checks,
            answer,
            sources,
            settings.groundedness_threshold,
        )
        guardrail_post_ms += (time.perf_counter() - t_post) * 1000

        logger.info(
            "groundedness request_id=%s attempt=%d grounded=%s confidence=%.4f threshold=%.2f flags=%s",
            request_id,
            attempt + 1,
            grounded,
            confidence,
            settings.groundedness_threshold,
            post_flags,
        )

        if grounded:
            break

    stage_timings["llm"] = (time.perf_counter() - t0) * 1000 - guardrail_post_ms
    stage_timings["guardrail_post"] = guardrail_post_ms

    # ---------------------------------------------------------
    # 5. LLM FAILURE / EMPTY RESPONSE
    # ---------------------------------------------------------

    if not answer.strip():
        flags = ["llm_call_failed"] if last_error else ["empty_llm_output"]

        logger.warning(
            "llm_empty_or_failed request_id=%s flags=%s",
            request_id,
            flags,
        )

        return _fallback_response(
            request_id=request_id,
            flags=flags,
            latency_ms=(time.perf_counter() - start) * 1000,
            sources=sources,
            follow_up_detected=follow_up_detected,
            history_turns_sent=history_turns_sent,
        )

    logger.debug(
        "llm_answer request_id=%s answer=%r",
        request_id,
        answer,
    )

    # ---------------------------------------------------------
    # 6. FINAL RESPONSE
    # ---------------------------------------------------------

    total_ms = (time.perf_counter() - start) * 1000

    if not grounded:
        return _fallback_response(
            request_id=request_id,
            flags=post_flags,
            latency_ms=total_ms,
            sources=sources,
            follow_up_detected=follow_up_detected,
            history_turns_sent=history_turns_sent,
        )

    return QueryResponse(
        answer=answer,
        sources=sources,
        grounded=grounded,
        confidence=confidence,
        request_id=request_id,
        latency_ms=total_ms,
        guardrail_flags=post_flags,
        follow_up_detected=follow_up_detected,
        history_turns_sent=history_turns_sent,
    )
