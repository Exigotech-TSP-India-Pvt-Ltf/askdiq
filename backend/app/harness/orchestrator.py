"""The harness: structured orchestration around the model.

Pipeline:
retrieve -> pre-guardrails -> retrieval relevance check ->
build prompt -> call LLM (with retry) ->
validate output -> post-guardrails -> structured response.

A short, ambiguous question may get a clarifying question with 2-4 options
instead of an answer (outcome ``clarify``, see app.harness.clarify). A
question with a referent that points at nothing, or an underspecified
compliance request, is asked back before the low-confidence fallback
(app.harness.ambiguity).
"""

import time

from app.core.circuit import CircuitOpenError
from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.evaluation.quality import injection_in_chunks
from app.governance.audit import emit_audit_event
from app.guardrails.conversational import detect_conversational_intent
from app.guardrails.post_checks import (
    is_off_topic_by_overlap,
    is_refusal,
    run_post_checks,
)
from app.guardrails.pre_checks import run_pre_checks, sanitize_query
from app.harness.ambiguity import assess, rule_clarification, service_clarification
from app.harness.answer_shape import detect_shape, refine_shape
from app.harness.citations import (
    TOKEN_DETAILS_KEY,
    apply_citations,
    citation_records,
    strip_free_brackets,
    strip_markers,
)
from app.harness.clarify import (
    ANSWER_OR_CLARIFY,
    CLARIFY_OUTCOME,
    boost_documents,
    clarify_eligible,
    clarify_instruction,
    parse_decision,
    plan_clarification,
    render_clarification_text,
)
from app.harness.prompts import _MAX_HISTORY_TURNS, call_llm
from app.harness.style import resolve_style, use_style
from app.harness.tone import polish_answer
from app.retrieval.retriever import needs_conversation_context, retrieve
from app.retrieval.vector_store import VectorStore
from app.schemas.query import ChatTurn, Clarification, QueryResponse, SourceChunk

logger = get_logger(__name__)

settings = get_settings()


class GuardrailBlockedError(Exception):
    """Raised when a guardrail blocks the request."""

    def __init__(self, flags: list[str]):
        self.flags = flags


_UNSAFE_FLAG = "unsafe_input_detected"
_SPECIALIST_STEP = "talk to one of our specialists"


def _fallback_answer(flags: list[str], brand: str) -> str:
    """The customer-facing wording for a blocked or non-answered request.

    Unsafe input wins over every other flag. Nothing here names the source of
    the answer (no "knowledge base", "documents" or "context"), and every
    not-covered or low-confidence reply ends with a human next step.
    """

    flagged = set(flags)
    if _UNSAFE_FLAG in flagged:
        return (
            "I can't help with that one, but I'm happy to answer questions about "
            f"{brand}'s security platform and compliance. For anything else, "
            f"{_SPECIALIST_STEP}."
        )
    if flagged & {"no_permission", "application_secret_request"}:
        return (
            "For security reasons I can't share credentials or other customers' "
            "information. One of our specialists can help with access."
        )
    if "personal_contact_request" in flagged:
        return (
            "I can't share individual contact details, but one of our specialists "
            "will be glad to help."
        )
    if "spam_detected" in flagged:
        return (
            "Sorry, I didn't catch that. Could you rephrase your question? I can "
            f"help with {brand}, Zero Trust, Essential Eight and more."
        )
    if "unclear_input" in flagged:
        return (
            "Could you add a little more detail so I can point you in the right "
            "direction?"
        )
    if "query_too_long" in flagged:
        return "That's a bit long for me. Could you shorten it to a sentence or two?"
    if "empty_query" in flagged:
        return "Please type a question and I'll do my best to help."
    if flagged & {"off_topic", "no_sources"}:
        return (
            "That's a bit outside what I can help with here. I can answer "
            f"questions about {brand}, Zero Trust, Essential Eight, Responsible AI "
            "and data handling, and our specialists are happy to help with anything "
            "else."
        )
    if flagged & {"llm_call_failed", "empty_llm_output"}:
        return (
            "Sorry, something went wrong on our side. Please try again in a "
            f"moment, or {_SPECIALIST_STEP} if it keeps happening."
        )
    # Ungrounded answers (low retrieval confidence, possible hallucination,
    # unsupported figures): the question was fine, the evidence was not.
    return (
        "I want to give you an accurate answer and I'm not certain on this one. "
        "Could you tell me a bit more about what you're after? Or "
        f"{_SPECIALIST_STEP}."
    )


def _fallback_response(
    request_id: str,
    flags: list[str],
    latency_ms: float,
    sources: list[SourceChunk] | None = None,
    follow_up_detected: bool = False,
    history_turns_sent: int = 0,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    reasoning_tokens: int | None = None,
    outcome: str = "blocked",
    token_details: dict | None = None,
) -> QueryResponse:
    """Return a safe, polite fallback response.

    Spam/unsafe input is blocked with dedicated polite guidance and never
    carries retrieval evidence. Genuine off-topic/low-confidence cases still
    surface whatever sources were considered, for transparency.
    """

    answer = _fallback_answer(flags, settings.brand_name)

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
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        reasoning_tokens=reasoning_tokens,
        outcome=outcome,
        token_details=token_details,
    )


def _clarification_response(
    clarification: Clarification,
    *,
    request_id: str,
    latency_ms: float,
    sources: list[SourceChunk],
    confidence: float,
    follow_up_detected: bool,
    history_turns_sent: int,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    total_tokens: int | None,
    reasoning_tokens: int | None,
    token_details: dict[str, object],
) -> QueryResponse:
    """A clarifying question: options persisted in token_details, text fallback in answer."""
    token_details["outcome"] = CLARIFY_OUTCOME
    token_details["clarification"] = clarification.model_dump()
    logger.info(
        "clarification_requested",
        questions=len(clarification.questions),
        documents=sorted(
            {
                d
                for q in clarification.questions
                for o in q.options
                for d in o.document_ids
            }
        ),
        latency_ms=round(latency_ms, 2),
    )
    return QueryResponse(
        answer=render_clarification_text(clarification),
        # Kept for the trace (which evidence was ambiguous); the UI shows no
        # citation chips for a non-answer.
        sources=sources,
        # The options come straight from retrieved documents, nothing invented.
        grounded=True,
        confidence=round(confidence, 4),
        request_id=request_id,
        latency_ms=latency_ms,
        guardrail_flags=[],
        follow_up_detected=follow_up_detected,
        history_turns_sent=history_turns_sent,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        reasoning_tokens=reasoning_tokens,
        outcome=CLARIFY_OUTCOME,
        token_details=token_details,
        clarification=clarification,
    )


async def run_query(
    query: str,
    top_k: int,
    filters: dict | None,
    vector_store: VectorStore,
    request_id: str,
    stage_timings: dict,
    history: list[ChatTurn] | None = None,
    boost_document_ids: list[str] | None = None,
    clarification_reply: bool = False,
    style_key: str | None = None,
) -> QueryResponse:
    """Run the complete RAG query pipeline.

    ``boost_document_ids``: documents of the clarification options the user
    picked; their chunks are put first in the answer context.
    ``clarification_reply``: the query already carries the answers to a
    clarification, so it is never asked back again.
    ``style_key``: the session id; it picks the response-style A/B bucket.

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
        latency_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "conversational_intercept",
            query_len=len(query),
            latency_ms=round(latency_ms, 2),
        )
        return QueryResponse(
            answer=conversational_reply,
            sources=[],
            grounded=True,
            confidence=1.0,
            request_id=request_id,
            latency_ms=latency_ms,
            guardrail_flags=[],
            follow_up_detected=follow_up_detected,
            history_turns_sent=history_turns_sent,
            outcome="conversational",
        )

    # ---------------------------------------------------------
    # 1. PRE-GUARDRAILS & SANITATION
    # ---------------------------------------------------------

    t0 = time.perf_counter()

    pre_flags = run_pre_checks(query, settings.max_query_length)
    if _UNSAFE_FLAG in pre_flags:
        # Unsafe input wins over every other flag: canned refusal only, with
        # no retrieval, no LLM call, no sources and no pitch.
        pre_flags = [_UNSAFE_FLAG]
    if pre_flags:
        latency_ms = (time.perf_counter() - start) * 1000
        logger.warning(
            "guardrail_blocked",
            stage="pre_checks",
            flags=pre_flags,
            query_snippet=query[:60],
            latency_ms=round(latency_ms, 2),
        )
        return _fallback_response(
            request_id=request_id,
            flags=pre_flags,
            latency_ms=latency_ms,
            follow_up_detected=follow_up_detected,
            history_turns_sent=history_turns_sent,
        )

    effective_query, sanitize_flags = sanitize_query(query)
    if sanitize_flags:
        latency_ms = (time.perf_counter() - start) * 1000
        logger.warning(
            "guardrail_blocked",
            stage="sanitize_query",
            flags=sanitize_flags,
            query_snippet=query[:60],
            latency_ms=round(latency_ms, 2),
        )
        return _fallback_response(
            request_id=request_id,
            flags=sanitize_flags,
            latency_ms=latency_ms,
            follow_up_detected=follow_up_detected,
            history_turns_sent=history_turns_sent,
        )

    stage_timings["guardrail_pre"] = (time.perf_counter() - t0) * 1000

    # ---------------------------------------------------------
    # 2. VECTOR RETRIEVAL
    # ---------------------------------------------------------

    t0 = time.perf_counter()

    # A short question that may need clarifying is retrieved wider (more
    # documents), so any options cover the knowledge base's real intents.
    ambiguity = assess(query, history, clarification_reply=clarification_reply)
    gated = not clarification_reply and clarify_eligible(
        query, history, follow_up=follow_up_detected
    )
    wide = gated or ambiguity.detected
    sources: list[SourceChunk] = await retrieve(
        vector_store,
        effective_query,
        top_k=max(top_k, settings.clarify_retrieval_top_k) if wide else top_k,
        filters=filters,
        history=history,
        **({"max_documents": settings.clarify_retrieval_max_documents} if wide else {}),
    )
    # Reply to a clarification: the chosen options' documents lead the context.
    sources = boost_documents(sources, boost_document_ids)

    stage_timings["retrieval"] = (time.perf_counter() - t0) * 1000

    # ---------------------------------------------------------
    # 3. RETRIEVAL RELEVANCE & INDIRECT INJECTION CHECK
    # ---------------------------------------------------------

    if not sources:
        latency_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "retrieval_empty",
            reason="no_sources_returned",
            query_len=len(query),
            latency_ms=round(latency_ms, 2),
        )
        # Distinct from `off_topic` (low relevance): nothing was retrieved at all,
        # which usually means an empty knowledge base or a filter that matches no chunk.
        return _fallback_response(
            request_id=request_id,
            flags=["no_sources"],
            latency_ms=latency_ms,
            follow_up_detected=follow_up_detected,
            history_turns_sent=history_turns_sent,
        )

    max_retrieval_score = max(source.score for source in sources)
    stage_timings["retrieval_relevance_score"] = max_retrieval_score

    decision_usage: dict[str, int] | None = None
    # Rule-detected ambiguity asks BEFORE the low-confidence fallback: a vague
    # question's evidence is spread thin over several documents by nature.
    if ambiguity.detected:
        t_clarify = time.perf_counter()
        ruled = await rule_clarification(ambiguity, query, sources)
        decision_usage = ruled.usage
        stage_timings["clarify_decision"] = (time.perf_counter() - t_clarify) * 1000
        logger.info(
            "clarify_rules",
            kind=ambiguity.kind.value,
            missing_slots=list(ambiguity.missing_slots),
            decision=ruled.decision,
            asked=ruled.clarification is not None,
        )
        if ruled.clarification is not None:
            usage = ruled.usage or {}
            prompt_tok = usage.get("prompt_tokens")
            completion_tok = usage.get("completion_tokens")
            return _clarification_response(
                ruled.clarification,
                request_id=request_id,
                latency_ms=(time.perf_counter() - start) * 1000,
                sources=sources,
                confidence=max_retrieval_score,
                follow_up_detected=follow_up_detected,
                history_turns_sent=history_turns_sent,
                prompt_tokens=prompt_tok,
                completion_tokens=completion_tok,
                total_tokens=(prompt_tok or 0) + (completion_tok or 0)
                if ruled.usage
                else None,
                reasoning_tokens=None,
                token_details=(
                    {"clarify_decision": ruled.usage, "cached_tokens": 0}
                    if ruled.usage
                    else {}
                ),
            )

    if max_retrieval_score < settings.min_retrieval_score or is_off_topic_by_overlap(
        effective_query,
        (s.content for s in sources),
        max_retrieval_score,
        settings.offtopic_overlap_max_score,
        follow_up=follow_up_detected or bool(history),
    ):
        latency_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "retrieval_low_confidence",
            max_score=round(max_retrieval_score, 4),
            threshold=settings.min_retrieval_score,
            query_len=len(query),
            latency_ms=round(latency_ms, 2),
        )
        return _fallback_response(
            request_id=request_id,
            flags=["off_topic"],
            latency_ms=latency_ms,
            sources=sources,
            follow_up_detected=follow_up_detected,
            history_turns_sent=history_turns_sent,
        )

    # Live Indirect Prompt Injection Defense (OWASP Top 10 LLM01)
    poisoned_indices = injection_in_chunks([s.content for s in sources])
    if poisoned_indices:
        logger.warning(
            "indirect_injection_detected",
            poisoned_count=len(poisoned_indices),
            poisoned_documents=[sources[i].document_id for i in poisoned_indices],
        )
        sources = [s for i, s in enumerate(sources) if i not in set(poisoned_indices)]
        if not sources:
            latency_ms = (time.perf_counter() - start) * 1000
            return _fallback_response(
                request_id=request_id,
                flags=["unsafe_input_detected"],
                latency_ms=latency_ms,
                follow_up_detected=follow_up_detected,
                history_turns_sent=history_turns_sent,
            )

    logger.info(
        "retrieval_success",
        query_len=len(query),
        sources=len(sources),
        max_score=round(max_retrieval_score, 4),
        documents=sorted({s.document_id for s in sources}),
    )

    # Short question + evidence spread over several similar-scoring topics:
    # the model may ask which one is meant instead of answering (same call).
    clarify_candidates = (
        plan_clarification(query, sources, history, follow_up=follow_up_detected)
        if gated
        else []
    )
    llm_kwargs: dict[str, object] = (
        {
            "clarify_block": clarify_instruction(clarify_candidates),
            "response_format": ANSWER_OR_CLARIFY,
        }
        if clarify_candidates
        else {}
    )
    clarification: Clarification | None = None

    # Response style (OR-8): rule-detected shape, sticky A/B variant per session.
    answer_style = resolve_style(style_key)
    shape = refine_shape(
        detect_shape(query, ambiguity),
        query,
        sources,
        min_score=settings.min_retrieval_score,
    )
    use_style(answer_style, shape)

    # ---------------------------------------------------------
    # 4. LLM GENERATION (+ POST-GENERATION GROUNDEDNESS CHECK)
    # ---------------------------------------------------------
    #
    # Retries cover only LLM errors/empty output; an ungrounded answer is not
    # re-sampled (saves a full LLM call + embedding on those requests).
    # ---------------------------------------------------------

    t0 = time.perf_counter()

    answer = ""
    last_error: Exception | None = None
    grounded = False
    confidence = 0.0
    post_flags: list[str] = []
    guardrail_post_ms = 0.0
    attempts_usage: list[dict] = []
    # The decision call (if any) is billed with the answer it led to.
    tot_prompt = (decision_usage or {}).get("prompt_tokens", 0)
    tot_completion = (decision_usage or {}).get("completion_tokens", 0)
    tot_reasoning = 0
    tot_cached = 0

    # An ungrounded clarification earns one direct-answer attempt on top.
    max_attempts = settings.max_llm_retries + 1 + (1 if clarify_candidates else 0)
    retry_reason = ""
    for attempt in range(max_attempts):
        if attempt > 0:
            emit_audit_event(
                "llm_retry",
                {
                    "attempt": attempt + 1,
                    "max_attempts": max_attempts,
                    "reason": retry_reason,
                },
                request_id=request_id,
            )
        try:
            attempt_answer, usage = await call_llm(
                query,
                sources,
                history,
                **llm_kwargs,
            )
            if usage:
                attempts_usage.append({"attempt": attempt + 1, "usage": usage})
                tot_prompt += usage.get("prompt_tokens", 0)
                tot_completion += usage.get("completion_tokens", 0)
                tot_reasoning += usage.get("reasoning_tokens", 0)
                tot_cached += usage.get("cached_tokens", 0)
            answer = attempt_answer
            if clarify_candidates:
                # Strict JSON: either the answer, or questions back.
                decision = parse_decision(answer, clarify_candidates, query)
                answer = decision.answer
                clarification = decision.clarification
        except Exception as exc:
            last_error = exc
            answer = ""
            retry_reason = "llm_call_failed"

            logger.error(
                "llm_call_failed request_id=%s attempt=%d error_type=%s error=%s",
                request_id,
                attempt + 1,
                type(exc).__name__,
                exc,
            )
            emit_audit_event(
                "llm_call_failed",
                {
                    "attempt": attempt + 1,
                    "max_attempts": max_attempts,
                    "error_type": type(exc).__name__,
                    "status_code": getattr(exc, "status_code", None),
                    "will_retry": attempt + 1 < max_attempts
                    and not isinstance(exc, CircuitOpenError),
                },
                request_id=request_id,
            )
            if isinstance(exc, CircuitOpenError):
                # Breaker is open: retrying now would fail fast again.
                break
            continue

        if clarification is not None:
            # Questions back to the user: nothing to ground-check.
            break

        if not answer.strip() and clarify_candidates:
            # Asked, but no option was grounded in the retrieved context:
            # answer directly on the next attempt instead.
            clarify_candidates = []
            llm_kwargs = {}
            retry_reason = "clarification_ungrounded"
            continue

        if not answer.strip():
            retry_reason = "empty_llm_output"
            continue

        t_post = time.perf_counter()
        # Citation markers are not evidence: check the plain text.
        grounded, confidence, post_flags = await run_post_checks(
            strip_markers(answer),
            sources,
            settings.groundedness_threshold,
            settings.min_retrieval_score,
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

        break

    stage_timings["llm"] = (time.perf_counter() - t0) * 1000 - guardrail_post_ms
    stage_timings["guardrail_post"] = guardrail_post_ms

    tot_total = tot_prompt + tot_completion
    has_llm_usage = len(attempts_usage) > 0 or decision_usage is not None
    prompt_tokens = tot_prompt if has_llm_usage else None
    completion_tokens = tot_completion if has_llm_usage else None
    total_tokens = tot_total if has_llm_usage else None
    reasoning_tokens = tot_reasoning if has_llm_usage else None

    token_details: dict[str, object] = (
        {
            "llm_attempts": len(attempts_usage),
            "attempts": attempts_usage,
            "cached_tokens": tot_cached,
            **({"clarify_decision": decision_usage} if decision_usage else {}),
        }
        if has_llm_usage
        else {}
    )
    token_details.update(style=answer_style.value, shape=shape.value)
    if clarification is None:
        answer = polish_answer(answer, answer_style)

    # ---------------------------------------------------------
    # 5. LLM FAILURE / EMPTY RESPONSE
    # ---------------------------------------------------------

    if not answer.strip() and clarification is None:
        flags = ["llm_call_failed"] if last_error else ["empty_llm_output"]
        outcome = "llm_failed" if last_error else "empty_llm_output"
        token_details["outcome"] = outcome

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
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            reasoning_tokens=reasoning_tokens,
            outcome=outcome,
            token_details=token_details or None,
        )

    if clarification is not None:
        return _clarification_response(
            clarification,
            request_id=request_id,
            latency_ms=(time.perf_counter() - start) * 1000,
            sources=sources,
            confidence=max_retrieval_score,
            follow_up_detected=follow_up_detected,
            history_turns_sent=history_turns_sent,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            reasoning_tokens=reasoning_tokens,
            token_details=token_details,
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

    if not grounded and gated:
        # A vague short question whose best guess is unsupported: ask which
        # service is meant rather than "couldn't find enough support".
        clarification = service_clarification(query, sources)
        if clarification is not None:
            return _clarification_response(
                clarification,
                request_id=request_id,
                latency_ms=total_ms,
                sources=sources,
                confidence=max_retrieval_score,
                follow_up_detected=follow_up_detected,
                history_turns_sent=history_turns_sent,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                reasoning_tokens=reasoning_tokens,
                token_details=token_details,
            )

    if not grounded:
        outcome = "ungrounded"
        token_details["outcome"] = outcome
        logger.warning(
            "groundedness_failed",
            outcome=outcome,
            flags=post_flags,
            confidence=round(confidence, 4),
            latency_ms=round(total_ms, 2),
            sources=len(sources),
        )
        return _fallback_response(
            request_id=request_id,
            flags=post_flags,
            latency_ms=total_ms,
            sources=sources,
            follow_up_detected=follow_up_detected,
            history_turns_sent=history_turns_sent,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            reasoning_tokens=reasoning_tokens,
            outcome=outcome,
            token_details=token_details or None,
        )

    plain_answer = strip_markers(answer)
    if is_refusal(plain_answer):
        # A decline ("not covered", "talk to a specialist") is not a grounded
        # answer: label it like other unanswered turns (grounded=False), and
        # never attach the retrieved chunks as sources or citation markers.
        grounded = False
        outcome = "ungrounded"
        token_details["outcome"] = outcome
        token_details["declined"] = True
        logger.info(
            "answer_declined",
            outcome=outcome,
            confidence=round(confidence, 4),
            latency_ms=round(total_ms, 2),
        )
        return QueryResponse(
            answer=strip_free_brackets(plain_answer),
            sources=[],
            citations=[],
            grounded=grounded,
            confidence=confidence,
            request_id=request_id,
            latency_ms=total_ms,
            guardrail_flags=post_flags,
            follow_up_detected=follow_up_detected,
            history_turns_sent=history_turns_sent,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            reasoning_tokens=reasoning_tokens,
            outcome=outcome,
            token_details=token_details or None,
        )

    outcome = "answered"
    token_details["outcome"] = outcome
    # Markers -> citations of retrieved sources only (URLs never from the text).
    answer, citations = apply_citations(answer, sources)
    if citations:
        token_details[TOKEN_DETAILS_KEY] = citation_records(citations)
    logger.info(
        "query_completed",
        outcome=outcome,
        grounded=True,
        confidence=round(confidence, 4),
        latency_ms=round(total_ms, 2),
        sources=len(sources),
        prompt_tok=prompt_tokens or 0,
        comp_tok=completion_tokens or 0,
        reasoning_tok=reasoning_tokens or 0,
    )

    return QueryResponse(
        answer=answer,
        sources=sources,
        citations=citations,
        grounded=grounded,
        confidence=confidence,
        request_id=request_id,
        latency_ms=total_ms,
        guardrail_flags=post_flags,
        follow_up_detected=follow_up_detected,
        history_turns_sent=history_turns_sent,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        reasoning_tokens=reasoning_tokens,
        outcome=outcome,
        token_details=token_details or None,
    )
