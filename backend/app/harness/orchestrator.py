# pylint: disable=too-many-lines
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

import asyncio
import re
import time
from dataclasses import replace
from urllib.parse import urlparse

import httpx
import openai

from app.core.circuit import CircuitOpenError
from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.evaluation.quality import injection_in_chunks
from app.governance.audit import emit_audit_event
from app.guardrails import intent_examples
from app.guardrails.conversational import (
    IDENTITY_POLICY,
    PERSONA_REPLY_KEYS,
    canned_reply_for,
    detect_acknowledgement,
    detect_contact_request,
    detect_conversational_intent,
    detect_identity_question,
    detect_incident_request,
    detect_persona,
    pre_llm_policy,
)
from app.guardrails.intent import IntentMatch, classify_intent
from app.guardrails.normalize import normalise_input
from app.guardrails.pre_checks import run_pre_checks, sanitize_query
from app.guardrails.task_type import task_type_policy
from app.harness.ambiguity import assess
from app.harness.answer_shape import detect_shape, refine_shape
from app.harness.citations import (
    TOKEN_DETAILS_KEY,
    citation_records,
)
from app.harness.claims import (
    Draft,
    GroundedAnswer,
    Part,
    SalesContact,
    find_sales_contact,
    ground_answer,
    overview_draft,
    parse_draft,
)
from app.harness.clarify import (
    CLARIFY_OUTCOME,
    boost_documents,
    catalogue_documents,
    clarify_allowed,
    decide_clarification,
    is_buying,
    render_clarification_text,
    service_card,
)
from app.harness.decision import Intent, Route, RouteDecision
from app.harness.page_links import with_page_link
from app.harness.persona import persona_response
from app.harness.prompt_catalog import (
    POLICY_NONE,
    POLICY_OFF_TOPIC,
    POLICY_TEMPLATE_IDS,
    PRICING_LINE,
    PROMPT_VERSION,
    covered_retry_block,
    fallback_line,
    policy_template,
    refusal_template_id,
    reply_policy,
)
from app.harness.prompts import _MAX_HISTORY_TURNS, NO_ANSWER_LINE, call_llm
from app.harness.style import resolve_style, use_style
from app.retrieval.reranker import entity_documents, entity_mentions
from app.retrieval.retriever import (
    covered_sources,
    decide_route,
    decompose_query,
    needs_conversation_context,
    retrieve_parts,
    route_sources,
    trust_clarified,
)
from app.retrieval.vector_store import VectorStore
from app.schemas.query import ChatTurn, Clarification, QueryResponse, SourceChunk

logger = get_logger(__name__)

settings = get_settings()


class GuardrailBlockedError(Exception):
    """Raised when a guardrail blocks the request."""

    def __init__(self, flags: list[str]):
        self.flags = flags


_UNSAFE_FLAG = "unsafe_input_detected"

# The only reply for anything our facts do not answer (ungrounded, declined,
# off-topic, nothing retrieved). No specialist mention: it must never imply
# we can help with something outside what we do.
NO_ANSWER = NO_ANSWER_LINE

# What the answer call may raise; anything else is a bug and propagates.
_LLM_ERRORS = (openai.OpenAIError, CircuitOpenError, TimeoutError, httpx.HTTPError)
# Dropped-claim reasons that are not a failed check (nothing was asserted).
_NOT_FAILURES = frozenset({"decline", "uncovered_part", "empty", "duplicate"})


def _fallback_answer(flags: list[str]) -> str:
    """The fixed customer-facing line for ``flags`` (app.harness.prompt_catalog)."""
    return fallback_line(flags)


def _fallback_response(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    request_id: str,
    flags: list[str],
    latency_ms: float,
    follow_up_detected: bool = False,
    history_turns_sent: int = 0,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    reasoning_tokens: int | None = None,
    outcome: str = "blocked",
    token_details: dict | None = None,
    sales_line: str | None = None,
    template_id: str | None = None,
) -> QueryResponse:
    """Return a safe, polite fallback response.

    ``sales_line``: buying intent with the KB contact chunk retrieved; it
    follows the no-answer line (still no sources or citations).
    ``template_id``: an approved POLICY template replaces the flag's line.
    Every fallback logs its template id as ``refusal_template`` (owner rule 5).

    A fallback is never an answer, so it carries no sources and no citations,
    whatever was retrieved (retrieval telemetry stays in logs and stage
    timings). Spam/unsafe/unclear input gets a short fixed text only.
    """

    # Anything not grounded in our facts gets the one fixed line, never the
    # model's own wording or a specialist hand-off.
    answer = NO_ANSWER if outcome == "ungrounded" else _fallback_answer(flags)
    if sales_line and outcome == "ungrounded":
        answer = f"{answer} {sales_line}"
    if template_id is not None:
        answer = policy_template(template_id)
    token_details = {
        **(token_details or {}),
        "refusal_template": template_id or refusal_template_id(flags, outcome),
    }

    return QueryResponse(
        answer=answer,
        sources=[],
        citations=[],
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


# A price / quote ask on a buying message: the KB publishes no prices.
_PRICE_ASK_RE = re.compile(
    r"\b(?:how\s{1,3}much|prices?|pricing|priced|costs?|costing|quotes?|"
    r"quotation|fees?)\b",
    re.IGNORECASE,
)
_SALES_ASK_RE = re.compile(
    r"\b(?:talk|speak|chat)\s{1,3}(?:to|with)\s{1,3}(?:(?:your|the|a)\s{1,3})?"
    r"sales\b|\bsales\s{1,3}(?:team|rep|representative)s?\b",
    re.IGNORECASE,
)
# Added to a buying ask sent to the model: price and purchase steps are not
# in the facts, so without it the model declines the whole question.
_BUYING_FOLLOW_UP = "Also, what is it and what does it include?"


def _url_path(url: str | None) -> str:
    return urlparse(url or "").path.rstrip("/")


async def _with_sales_contact(
    vector_store: VectorStore, sources: list[SourceChunk]
) -> tuple[list[SourceChunk], SalesContact | None]:
    """Buying intent: ``sources`` plus the KB contact page chunk, and the
    phone/email read from it.

    The contact page (``settings.sales_contact_route``) is fetched by route
    when retrieval did not return it; the values always come from that
    chunk, never from code. No contact chunk in the KB -> ``None``.
    """
    route = settings.sales_contact_route.rstrip("/")
    page = [s for s in sources if _url_path(s.url) == route]
    if find_sales_contact(page) is None:
        page = await route_sources(vector_store, settings.sales_contact_route)
    found = find_sales_contact(page)
    if found is None:
        logger.warning("sales_contact_missing", route=route)
        return sources, None
    chunk = page[found.source - 1]
    merged = [s for s in sources if s.chunk_id != chunk.chunk_id]
    merged.append(chunk)
    return merged, SalesContact(len(merged), found.phone, found.email)


def _one_buying_part(draft: Draft, query: str) -> Draft:
    """A covered buying ask as one covered part: the model's price / purchase
    sub-ask is answered by the sales (and pricing) line, so no claim of the
    service's facts is dropped and no partial line names it."""
    claims = [replace(c, part=0) for c in draft.claims]
    return Draft(draft.action, [Part(query, True)], claims, draft.mode)


def _with_pricing_line(
    text: str, query: str, uncovered: bool, sales_line: str | None = None
) -> str:
    """A price ask answered from the service page: say pricing is not
    published (once; a partial line already names the gap), before the
    sales line when the reply has one."""
    if uncovered or not _PRICE_ASK_RE.search(query):
        return text
    at = text.rfind(sales_line) if sales_line else -1
    if at < 0:
        return f"{text}\n\n{PRICING_LINE}"
    return f"{text[:at]}{PRICING_LINE} {text[at:]}"


def _clarification_response(  # pylint: disable=too-many-arguments
    clarification: Clarification,
    *,
    request_id: str,
    latency_ms: float,
    sources: list[SourceChunk],  # noqa: ARG001  # pylint: disable=unused-argument  # kept for callers; cards carry none
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
        retrieval_score=round(confidence, 4),
    )
    return QueryResponse(
        answer=render_clarification_text(clarification),
        # A question is not an answer: no sources or citation chips. The
        # options keep their document ids inside the clarification payload.
        sources=[],
        # A question back is not a grounded answer (the score stays in logs).
        grounded=False,
        confidence=0.0,
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


def _canned_response(
    answer: str,
    *,
    request_id: str,
    start: float,
    follow_up_detected: bool,
    history_turns_sent: int,
) -> QueryResponse:
    """A fixed reply: no retrieval, no LLM, no sources, no citations."""
    latency_ms = (time.perf_counter() - start) * 1000
    logger.info("canned_reply_intercept", latency_ms=round(latency_ms, 2))
    return QueryResponse(
        answer=answer,
        sources=[],
        citations=[],
        # A fixed reply is not evidence-backed: never counted as grounded.
        grounded=False,
        confidence=0.0,
        request_id=request_id,
        latency_ms=latency_ms,
        guardrail_flags=[],
        follow_up_detected=follow_up_detected,
        history_turns_sent=history_turns_sent,
        outcome="conversational",
    )


def _policy_decline(  # pylint: disable=too-many-arguments
    policy: tuple[str, str],
    *,
    request_id: str,
    start: float,
    follow_up_detected: bool,
    history_turns_sent: int,
    token_details: dict | None = None,
) -> QueryResponse:
    """A pre-LLM POLICY refusal (identity probe / regulated advice): the
    approved template only, blocked, no sources."""
    template_id, flag = policy
    logger.info("policy_refusal", request_id=request_id, policy=flag, stage="pre_llm")
    return _fallback_response(
        request_id=request_id,
        flags=[flag],
        latency_ms=(time.perf_counter() - start) * 1000,
        follow_up_detected=follow_up_detected,
        history_turns_sent=history_turns_sent,
        outcome="blocked",
        token_details={
            **(token_details or {}),
            "outcome": "blocked",
            "policy": template_id,
        },
        template_id=template_id,
    )


def _canned_reply(query: str) -> str | None:
    """Fixed, no-LLM reply for contact requests.

    Pricing is routed (phase 4): buying without a named service gets the
    "which service?" card, buying with one is answered with the sales contact.
    """
    return detect_contact_request(query)


def _last_assistant_was_clarification(history: list[ChatTurn] | None) -> bool:
    for turn in reversed(history or []):
        if turn.role == "assistant":
            return turn.outcome == CLARIFY_OUTCOME
    return False


def _anchored_reply(
    query: str, history: list[ChatTurn] | None, clarification_reply: bool
) -> str | None:
    """Fixed replies from whole-message patterns (no embedding call):
    incident, contact, identity, then a bare acknowledgement.

    A bare "yes"/"ok" is never intercepted when it answers a clarification.
    """
    reply = (
        detect_incident_request(query)
        or detect_contact_request(query)
        or detect_identity_question(query)
    )
    if reply is not None:
        return reply
    if clarification_reply or _last_assistant_was_clarification(history):
        return None
    return detect_acknowledgement(query)


def _classifier_reply(
    query: str, history: list[ChatTurn] | None, match: IntentMatch
) -> str | None:
    """The intent classifier's canned reply: only for a *pure* intent
    (support, callback, complaint, assurance, small talk)."""
    reply_key = match.reply_key
    if reply_key == intent_examples.ACK and _last_assistant_was_clarification(history):
        return None
    return canned_reply_for(reply_key, query)


@with_page_link
async def run_query(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals,too-many-return-statements,too-many-branches,too-many-statements
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

    # One canonical form for every gate and for retrieval (NFKC, zero-width
    # chars, markup, homoglyphs, whitespace); the raw text is kept for the
    # security checks and the audit log.
    raw_query = query
    query = normalise_input(raw_query)
    if query != raw_query:
        logger.info(
            "input_normalised",
            raw_len=len(raw_query),
            normalised_len=len(query),
            raw_snippet=raw_query[:60],
        )

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

    # PERSONA (name, maker, model, languages, chat privacy, memory, human...):
    # its own approved template (app.harness.persona); anchored patterns only.
    persona = None if clarification_reply else detect_persona(query)
    if persona is not None:
        return await persona_response(
            persona,
            vector_store=vector_store,
            request_id=request_id,
            start=start,
            follow_up_detected=follow_up_detected,
            history_turns_sent=history_turns_sent,
        )

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
            grounded=False,
            confidence=0.0,
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

    pre_flags = run_pre_checks(query, settings.max_query_length, raw=raw_query)
    if _UNSAFE_FLAG in pre_flags:
        # Unsafe input wins over every other flag: canned refusal only, with
        # no retrieval, no LLM call, no sources and no pitch.
        pre_flags = [_UNSAFE_FLAG]
    # Priority: incident, contact, identity, acknowledgement (anchored
    # whole-message patterns, no embedding call), then the intent classifier
    # (embedding nearest-neighbour): a canned reply only for a *pure*
    # support/callback/complaint/assurance/small-talk message; anything naming
    # a product or service is retrieved. The intent stays on the request
    # context (app.guardrails.intent.get_current_intent) for later stages.
    # Blocked input never reaches these; a clarification answer never gets a
    # classifier reply. An identity probe ("are you a real person?", "do you
    # have feelings?") gets the approved identity template first (owner rule).
    policy_kwargs = {
        "request_id": request_id,
        "start": start,
        "follow_up_detected": follow_up_detected,
        "history_turns_sent": history_turns_sent,
    }
    # Then the task-type policy (app.guardrails.task_type): a pasted
    # credential, creative / translation / recommendation tasks and clear-cut
    # regulated advice get their template before retrieval can call them
    # off-domain or not covered.
    identity = (
        pre_llm_policy(query, regulated=False) or task_type_policy(query)
        if set(pre_flags) <= {"off_topic"} and not clarification_reply
        else None
    )
    if identity is not None:
        return _policy_decline(identity, **policy_kwargs)
    incident_reply = (
        _anchored_reply(query, history, clarification_reply)
        if set(pre_flags) <= {"off_topic"}
        else None
    )
    if incident_reply is None and not pre_flags:
        intent_match = await classify_intent(query)
        stage_timings["intent_classify"] = intent_match.ms
        if not clarification_reply:
            incident_reply = _classifier_reply(query, history, intent_match)
        if incident_reply is not None and (
            intent_match.reply_key == intent_examples.IDENTITY
        ):
            return _policy_decline(IDENTITY_POLICY, **policy_kwargs)
        if incident_reply is not None and intent_match.reply_key in PERSONA_REPLY_KEYS:
            return await persona_response(
                PERSONA_REPLY_KEYS[intent_match.reply_key],
                vector_store=vector_store,
                **policy_kwargs,
            )
    if incident_reply is not None:
        return _canned_response(
            incident_reply,
            request_id=request_id,
            start=start,
            follow_up_detected=follow_up_detected,
            history_turns_sent=history_turns_sent,
        )
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

    effective_query, sanitize_flags = sanitize_query(query, pre_flags)
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

    # A "how do I reach you" question is on-topic: a fixed, helpful reply with
    # a specialist step; no retrieval, no LLM, no invented contact details.
    contact_reply = _canned_reply(query)
    if contact_reply is not None:
        return _canned_response(
            contact_reply,
            request_id=request_id,
            start=start,
            follow_up_detected=follow_up_detected,
            history_turns_sent=history_turns_sent,
        )

    # ---------------------------------------------------------
    # 2. RETRIEVAL PER ASK + CALIBRATED RELEVANCE GATE (phase 4)
    # ---------------------------------------------------------
    # Every ask of the message is retrieved on its own and judged by the raw
    # cross-encoder logit of its best chunk (app.retrieval.retriever). The
    # resulting ``route_decision`` (ANSWER / PARTIAL / NOT_COVERED /
    # OFF_TOPIC, per-part verdicts, buying flag) is what generation consumes.

    t0 = time.perf_counter()

    # Shape hints only (comparison, how-to); it never asks anything back.
    ambiguity = assess(query, history, clarification_reply=True)
    buying = intent_match is not None and intent_match.result.intent is Intent.BUYING
    if intent_match is not None and not intent_match.result.score and not buying:
        # Classifier unavailable (it fails open to QUESTION, score 0): keep the
        # owner's buying rules working on the purchase wording alone.
        buying = is_buying(query)
    # "Talk to sales about X" is a buying ask whatever the classifier says.
    buying = buying or _SALES_ASK_RE.search(query) is not None
    asks = 1 if clarification_reply else len(decompose_query(effective_query))
    # A message that may get a "which service?" card is retrieved wider, so
    # its options cover every service page the question matches.
    wide = clarify_allowed(
        query, history, clarification_reply=clarification_reply, parts=asks
    )
    retrieval = await retrieve_parts(
        vector_store,
        effective_query,
        top_k=max(top_k, settings.clarify_retrieval_top_k) if wide else top_k,
        filters=filters,
        history=history,
        max_documents=settings.clarify_retrieval_max_documents if wide else None,
        split=not clarification_reply,
        buying=buying,
        prefer_documents=boost_document_ids if clarification_reply else None,
    )
    route_decision: RouteDecision = decide_route(retrieval.parts, buying=buying)
    if clarification_reply:
        # The card was built from KB pages this ask matched: its answer is
        # answered (quote-backed generation still drops unsupported claims)
        # unless the reply is off-domain.
        route_decision = trust_clarified(route_decision)
    # Covered asks' chunks only, in cross-encoder order; a reply to a
    # clarification puts the chosen options' documents first.
    sources: list[SourceChunk] = boost_documents(
        covered_sources(retrieval, route_decision), boost_document_ids
    )
    stage_timings["retrieval"] = (time.perf_counter() - t0) * 1000
    logger.info(
        "retrieval_gate",
        route=route_decision.route.value,
        reason=route_decision.reason,
        buying=buying,
        max_ce=retrieval.max_ce,
        parts=[
            {"text": v.text[:80], "covered": v.covered, "score": v.score}
            for v in route_decision.parts
        ],
    )

    # ---------------------------------------------------------
    # 3. EMPTY RETRIEVAL, CLARIFICATION, NOT COVERED, INJECTION
    # ---------------------------------------------------------

    if not retrieval.sources:
        latency_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "retrieval_empty",
            reason="no_sources_returned",
            query_len=len(query),
            latency_ms=round(latency_ms, 2),
        )
        # Nothing was retrieved at all: an empty knowledge base or a filter
        # that matches no chunk (distinct from a low-relevance ask).
        return _fallback_response(
            request_id=request_id,
            flags=["no_sources"],
            latency_ms=latency_ms,
            follow_up_detected=follow_up_detected,
            history_turns_sent=history_turns_sent,
        )

    max_retrieval_score = max(source.score for source in sources)
    stage_timings["retrieval_relevance_score"] = max_retrieval_score
    gate_details: dict[str, object] = {
        "route": route_decision.route.value,
        "route_reason": route_decision.reason,
        "parts": [
            {"text": v.text[:120], "covered": v.covered, "score": v.score}
            for v in route_decision.parts
        ],
    }

    # One deterministic clarification mechanism (owner rule): KB content that
    # is ambiguous across services, or buying without a named service.
    if wide:
        t_clarify = time.perf_counter()
        clarify = decide_clarification(
            query,
            history,
            sources=retrieval.sources,
            ce_by_chunk=retrieval.parts[0].ce_by_chunk,
            max_ce=retrieval.max_ce,
            route=route_decision.route.value,
            buying=buying,
            clarification_reply=clarification_reply,
            parts=len(retrieval.parts),
        )
        card = (
            service_card(
                query,
                clarify,
                await catalogue_documents(vector_store, retrieval.parts[0], filters),
            )
            if clarify.ask
            else None
        )
        stage_timings["clarify_decision"] = (time.perf_counter() - t_clarify) * 1000
        logger.info("clarify_decision", reason=clarify.reason, asked=card is not None)
        if card is not None:
            return _clarification_response(
                card,
                request_id=request_id,
                latency_ms=(time.perf_counter() - start) * 1000,
                sources=sources,
                confidence=max_retrieval_score,
                follow_up_detected=follow_up_detected,
                history_turns_sent=history_turns_sent,
                prompt_tokens=None,
                completion_tokens=None,
                total_tokens=None,
                reasoning_tokens=None,
                token_details={**gate_details, "clarify_reason": clarify.reason},
            )

    if route_decision.route in (Route.NOT_COVERED, Route.OFF_TOPIC):
        # Declined before the LLM: no ask clears the calibrated gate.
        latency_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "retrieval_low_confidence",
            route=route_decision.route.value,
            max_ce=retrieval.max_ce,
            max_score=round(max_retrieval_score, 4),
            query_len=len(query),
            latency_ms=round(latency_ms, 2),
        )
        # Identity probes and regulated-advice asks never reach the LLM's
        # policy field here: they get their approved template (owner rule).
        policy = pre_llm_policy(query, regulated=True)
        if policy is not None:
            return _policy_decline(policy, token_details=gate_details, **policy_kwargs)
        off_topic = route_decision.route is Route.OFF_TOPIC
        # A buying ask the KB does not cover still gets the public contact.
        _, declined_sales = (
            await _with_sales_contact(vector_store, [])
            if route_decision.buying and not off_topic
            else ([], None)
        )
        return _fallback_response(
            request_id=request_id,
            flags=["off_topic"] if off_topic else ["not_covered"],
            latency_ms=(time.perf_counter() - start) * 1000,
            follow_up_detected=follow_up_detected,
            history_turns_sent=history_turns_sent,
            outcome="blocked" if off_topic else "ungrounded",
            token_details=gate_details,
            sales_line=declined_sales.line if declined_sales else None,
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

    # Buying intent: the KB contact page chunk joins the facts (fetched by
    # route when retrieval missed it) so the sales line is read from and
    # cited to it.
    sales: SalesContact | None = None
    if route_decision.buying:
        sources, sales = await _with_sales_contact(vector_store, sources)

    logger.info(
        "retrieval_success",
        query_len=len(query),
        sources=len(sources),
        max_score=round(max_retrieval_score, 4),
        documents=sorted({s.document_id for s in sources}),
    )

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
    # 4. LLM GENERATION: quote-backed claims (strict JSON)
    # ---------------------------------------------------------
    #
    # Transport errors are retried once inside call_llm; here the call is
    # re-asked only on empty output (max_llm_retries). An unverified answer is
    # never re-sampled. The full question is sent: the model names the parts
    # the facts do not cover, and the partial line is built from those names.
    # ---------------------------------------------------------

    t0 = time.perf_counter()

    draft: Draft | None = None
    last_error: Exception | None = None
    attempts_usage: list[dict] = []
    tot_prompt = tot_completion = tot_reasoning = tot_cached = 0

    # A buying ask about a covered service: also ask what the service is, so
    # the reply carries its facts (the pricing and sales lines follow).
    buying_answer = route_decision.buying and route_decision.route is Route.ANSWER
    llm_question = f"{query} {_BUYING_FOLLOW_UP}" if buying_answer else query
    max_attempts = settings.max_llm_retries + 1
    for attempt in range(max_attempts):
        if attempt > 0:
            emit_audit_event(
                "llm_retry",
                {
                    "attempt": attempt + 1,
                    "max_attempts": max_attempts,
                    "reason": "empty_llm_output",
                },
                request_id=request_id,
            )
        try:
            raw_reply, usage = await call_llm(llm_question, sources, history)
        except _LLM_ERRORS as exc:
            # call_llm already retried transient failures (the one transport
            # retry layer); an error reaching here ends the attempts.
            last_error = exc
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
                    "will_retry": False,
                    "circuit_open": isinstance(exc, CircuitOpenError),
                },
                request_id=request_id,
            )
            break

        if usage:
            attempts_usage.append({"attempt": attempt + 1, "usage": usage})
            tot_prompt += usage.get("prompt_tokens", 0)
            tot_completion += usage.get("completion_tokens", 0)
            tot_reasoning += usage.get("reasoning_tokens", 0)
            tot_cached += usage.get("cached_tokens", 0)
        draft = parse_draft(raw_reply)
        if draft is not None:
            break

    stage_timings["llm"] = (time.perf_counter() - t0) * 1000

    has_llm_usage = len(attempts_usage) > 0
    prompt_tokens = tot_prompt if has_llm_usage else None
    completion_tokens = tot_completion if has_llm_usage else None
    total_tokens = tot_prompt + tot_completion if has_llm_usage else None
    reasoning_tokens = tot_reasoning if has_llm_usage else None

    token_details: dict[str, object] = (
        {
            "llm_attempts": len(attempts_usage),
            "attempts": attempts_usage,
            "cached_tokens": tot_cached,
        }
        if has_llm_usage
        else {}
    )
    token_details.update(
        gate_details,
        style=answer_style.value,
        shape=shape.value,
        prompt_version=PROMPT_VERSION,
    )

    # ---------------------------------------------------------
    # 5. LLM FAILURE / EMPTY RESPONSE
    # ---------------------------------------------------------

    # A bare catalogue topic whose own page was retrieved ("ISO 27001", "e8")
    # is covered by definition (see _topic_sources).
    topic = _topic_sources(query, sources, route_decision)
    if draft is None and topic:
        # No usable reply: the topic page's own sentences, verified below,
        # instead of the "try again" line (deterministic, no model needed).
        draft, raw_reply = overview_draft(sources, topic, query), ""
        token_details["topic_overview"] = True
        logger.info("topic_overview_no_llm_reply", request_id=request_id)

    if draft is None:
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
            follow_up_detected=follow_up_detected,
            history_turns_sent=history_turns_sent,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            reasoning_tokens=reasoning_tokens,
            outcome=outcome,
            token_details=token_details,
        )

    # POLICY (owner rules 1, 2, 6): the answer call flags identity probes,
    # regulated advice, injection and off-topic asks; each gets its approved
    # template only (no claims, no sources, no contact line).
    policy = reply_policy(raw_reply)
    if policy == POLICY_OFF_TOPIC and route_decision.route is Route.PARTIAL:
        # One off-topic ask beside a covered one never refuses the whole
        # reply: the covered retry answers the covered part and the partial
        # line names the off-topic one.
        logger.info("policy_off_topic_part", request_id=request_id)
        policy = POLICY_NONE
    if policy != POLICY_NONE:
        token_details["outcome"] = "blocked"
        token_details["policy"] = policy
        logger.info("policy_refusal", request_id=request_id, policy=policy)
        return _fallback_response(
            request_id=request_id,
            flags=[policy],
            latency_ms=(time.perf_counter() - start) * 1000,
            follow_up_detected=follow_up_detected,
            history_turns_sent=history_turns_sent,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            reasoning_tokens=reasoning_tokens,
            token_details=token_details,
            template_id=POLICY_TEMPLATE_IDS[policy],
        )

    # ---------------------------------------------------------
    # 6. VERIFY EVERY CLAIM AGAINST ITS QUOTE (deterministic)
    # ---------------------------------------------------------
    # This is the only grounded/ungrounded verdict: a reply ships only from
    # claims whose quote, numbers, names and negation match the cited chunk.

    t_post = time.perf_counter()
    # A bare topic is covered: the model may not mark it uncovered (claims
    # still verify).
    if topic:
        draft = _all_parts_covered(draft, query)
    if buying_answer:
        # Price / "how do I buy" is answered by the pricing and sales lines,
        # never by a "don't have an answer" line (every gate part is covered).
        draft = _one_buying_part(draft, query)
    try:
        grounded_answer = await asyncio.to_thread(
            ground_answer,
            draft,
            sources,
            query,
            numbered=shape.value == "how_to",
            # The gate found an ask the KB does not cover and the model listed
            # fewer parts than the gate: name "the rest" rather than drop it.
            gate_uncovered=route_decision.route is Route.PARTIAL
            and len(draft.parts) < len(route_decision.parts),
            gap_parts=[p.text for p in route_decision.uncovered_parts],
            sales_contact=sales,
        )
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught  # a verifier bug is an error, never a silent no-answer
        logger.exception("claim_verification_failed", request_id=request_id)
        token_details["outcome"] = "verification_error"
        return _fallback_response(
            request_id=request_id,
            flags=["verification_failed"],
            latency_ms=(time.perf_counter() - start) * 1000,
            follow_up_detected=follow_up_detected,
            history_turns_sent=history_turns_sent,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            reasoning_tokens=reasoning_tokens,
            outcome="verification_error",
            token_details=token_details,
        )
    stage_timings["guardrail_post"] = (time.perf_counter() - t_post) * 1000

    if _needs_covered_retry(grounded_answer, route_decision, topic):
        # The gate found evidence for a part (or the question is a bare topic
        # whose page was retrieved) but no claim verified for it: ask ONCE
        # more, pointing at that part and its chunks. Only a reply whose
        # claims verify is taken.
        t_retry = time.perf_counter()
        retried, retry_usage = await _covered_retry(
            query,
            sources,
            history,
            route_decision,
            grounded_answer,
            numbered=shape.value == "how_to",
            sales=sales,
            request_id=request_id,
            topic=topic,
        )
        stage_timings["llm_covered_retry"] = (time.perf_counter() - t_retry) * 1000
        if retry_usage:
            attempts_usage.append(
                {"attempt": len(attempts_usage) + 1, "usage": retry_usage}
            )
            prompt_tokens = (prompt_tokens or 0) + retry_usage.get("prompt_tokens", 0)
            completion_tokens = (completion_tokens or 0) + retry_usage.get(
                "completion_tokens", 0
            )
            reasoning_tokens = (reasoning_tokens or 0) + retry_usage.get(
                "reasoning_tokens", 0
            )
            total_tokens = prompt_tokens + completion_tokens
            token_details["llm_attempts"] = len(attempts_usage)
            token_details["attempts"] = attempts_usage
        token_details["covered_retry"] = retried is not None
        if retried is not None:
            grounded_answer = retried

    if topic and not grounded_answer.answered:
        # Deterministic last resort: the topic's page sentences, verbatim and
        # verified like any claim (gpt-5-mini cannot run at temperature 0).
        grounded_answer = await asyncio.to_thread(
            ground_answer,
            overview_draft(sources, topic, query),
            sources,
            query,
            numbered=shape.value == "how_to",
            sales_contact=sales,
        )
        token_details["topic_overview"] = grounded_answer.answered
        logger.info(
            "topic_overview_fallback",
            request_id=request_id,
            sources=topic,
            kept=len(grounded_answer.kept),
        )

    token_details["grounding"] = grounded_answer.details()
    total_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "claims_verified",
        request_id=request_id,
        mode=grounded_answer.mode,
        kept=len(grounded_answer.kept),
        dropped=[reason for _claim, reason in grounded_answer.dropped],
        uncovered=len(grounded_answer.uncovered),
        prompt_version=PROMPT_VERSION,
    )

    if not grounded_answer.answered:
        # Nothing verified: the fixed no-answer line only (no draft text,
        # no sources, no citations, confidence 0).
        failed = [r for _c, r in grounded_answer.dropped if r not in _NOT_FAILURES]
        post_flags = ["possible_hallucination"] if failed else ["not_covered"]
        outcome = "ungrounded"
        token_details["outcome"] = outcome
        logger.warning(
            "groundedness_failed",
            outcome=outcome,
            flags=post_flags,
            latency_ms=round(total_ms, 2),
            sources=len(sources),
        )
        return _fallback_response(
            request_id=request_id,
            flags=post_flags,
            latency_ms=total_ms,
            follow_up_detected=follow_up_detected,
            history_turns_sent=history_turns_sent,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            reasoning_tokens=reasoning_tokens,
            outcome=outcome,
            token_details=token_details,
            sales_line=sales.line if sales else None,
        )

    # ---------------------------------------------------------
    # 7. FINAL RESPONSE: only verified claims (+ the fixed partial line)
    # ---------------------------------------------------------

    outcome = "answered"
    token_details["outcome"] = outcome
    answer_text = grounded_answer.text
    if route_decision.buying:
        answer_text = _with_pricing_line(
            answer_text,
            query,
            uncovered=bool(grounded_answer.uncovered),
            sales_line=sales.line if sales else None,
        )
    partial = bool(grounded_answer.uncovered) or answer_text != grounded_answer.text
    post_flags = ["partial_answer"] if partial else []
    citations = grounded_answer.citations
    if citations:
        token_details[TOKEN_DETAILS_KEY] = citation_records(citations)
    confidence = grounded_answer.verified_ratio
    logger.info(
        "query_completed",
        outcome=outcome,
        grounded=True,
        confidence=confidence,
        latency_ms=round(total_ms, 2),
        sources=len(sources),
        cited_sources=len(grounded_answer.sources),
        retrieved_documents=sorted({s.document_id for s in sources}),
        retrieval_scores=[round(s.score, 4) for s in sources],
        prompt_tok=prompt_tokens or 0,
        comp_tok=completion_tokens or 0,
        reasoning_tok=reasoning_tokens or 0,
        prompt_version=PROMPT_VERSION,
    )

    return QueryResponse(
        answer=answer_text,
        sources=grounded_answer.sources,
        citations=citations,
        grounded=True,
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
        token_details=token_details,
    )


# ---------------------------------------------------------------------------
# Covered-part retry (generation region): the model sometimes returns no
# claim although the gate found evidence (gpt-5-mini has no temperature 0).
# ---------------------------------------------------------------------------
_MAX_FOCUS_SOURCES = 4
_MAX_TOPIC_SCAN_CHARS = 200
_TOPIC_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'&+-]{0,40}")
# What a bare-topic question may add around the topic ("tell me about e8").
_TOPIC_FILLER_WORDS = frozenset(
    {
        "tell", "me", "about", "what", "is", "are", "the", "a", "an",
        "explain", "describe", "overview", "info", "information", "more",
        "on", "your", "you", "do", "offer", "please", "details", "detail",
        "give",
    }
)  # fmt: skip
_TOPIC_NAME_KEYS = ("page_title", "section_path", "section_heading", "source_name")


def _source_name(source: SourceChunk) -> str:
    """The chunk's title, page title, section path and source name."""
    meta = source.metadata or {}
    names = [source.title or ""]
    for key in _TOPIC_NAME_KEYS:
        value = meta.get(key)
        names.extend(map(str, value) if isinstance(value, list) else [str(value or "")])
    return " ".join(names)


def _topic_sources(
    query: str, sources: list[SourceChunk], decision: RouteDecision
) -> list[int]:
    """1-based numbers of the retrieved chunks that are the page of the bare
    catalogue topic the question names ("ISO 27001", "tell me about e8").

    Deterministic, independent of the gate: the question is only topic names
    plus filler words, and a chunk's title / section path names that topic.
    ``[]`` for anything else (PCI DSS is no catalogue topic: never matched).
    """
    scan = " ".join(query.split())[:_MAX_TOPIC_SCAN_CHARS]
    mentions = entity_mentions(scan)
    if not mentions:
        return []
    rest = scan
    for start, end, _key in reversed(mentions):
        rest = f"{rest[:start]} {rest[end:]}"
    if any(w not in _TOPIC_FILLER_WORDS for w in _TOPIC_WORD_RE.findall(rest.lower())):
        return []
    names = {n: _source_name(s) for n, s in enumerate(sources, start=1)}
    keys = list(dict.fromkeys(key for _s, _e, key in mentions))
    numbers = sorted(int(n) for n in entity_documents(keys, names))
    if not numbers and decision.reason == "bare_topic":
        numbers = _focus_sources(sources, decision)
    return numbers[:_MAX_FOCUS_SOURCES]


def _all_parts_covered(draft: Draft, query: str) -> Draft:
    """The draft with every part covered (one part when the model named none)."""
    parts = [Part(p.ask, True) for p in draft.parts] or [Part(query, True)]
    return Draft(draft.action, parts, draft.claims, draft.mode)


def _needs_covered_retry(
    result: GroundedAnswer, decision: RouteDecision, topic: list[int]
) -> bool:
    if not (decision.covered_parts or topic):
        return False
    if not result.answered:
        return True
    # Every ask is covered per the gate, yet the model left a part without a
    # verified claim.
    return decision.route is Route.ANSWER and bool(result.uncovered)


def _focus_sources(sources: list[SourceChunk], decision: RouteDecision) -> list[int]:
    """1-based numbers of the chunks that back the gate's covered parts."""
    wanted = {d for p in decision.covered_parts for d in p.document_ids}
    numbers = [i for i, s in enumerate(sources, start=1) if s.document_id in wanted]
    return (numbers or list(range(1, len(sources) + 1)))[:_MAX_FOCUS_SOURCES]


async def _covered_retry(  # pylint: disable=too-many-arguments,too-many-locals
    query: str,
    sources: list[SourceChunk],
    history: list[ChatTurn] | None,
    decision: RouteDecision,
    current: GroundedAnswer,
    *,
    numbered: bool,
    sales: SalesContact | None,
    request_id: str,
    topic: list[int],
) -> tuple[GroundedAnswer | None, dict[str, int] | None]:
    """One targeted re-ask; the new answer only if it verifies better.

    ``topic``: the bare topic's page chunks; the re-ask then asks for an
    overview from them and the reply's parts count as covered.
    """
    asks = current.uncovered if current.answered else []
    asks = asks or [p.text for p in decision.covered_parts] or [query]
    block = covered_retry_block(
        asks, topic or _focus_sources(sources, decision), overview=bool(topic)
    )
    try:
        raw_reply, usage = await call_llm(query, sources, history, focus_block=block)
    except _LLM_ERRORS as exc:
        logger.warning(
            "covered_retry_failed", request_id=request_id, error_type=type(exc).__name__
        )
        return None, None
    draft = parse_draft(raw_reply)
    if draft is None:
        return None, usage
    if topic:
        draft = _all_parts_covered(draft, query)
    result = await asyncio.to_thread(
        ground_answer,
        draft,
        sources,
        query,
        numbered=numbered,
        gap_parts=[p.text for p in decision.uncovered_parts],
        sales_contact=sales,
    )
    better = result.answered and (
        not current.answered or len(result.uncovered) < len(current.uncovered)
    )
    logger.info(
        "covered_retry",
        request_id=request_id,
        kept=len(result.kept),
        taken=better,
        dropped=[reason for _claim, reason in result.dropped],
    )
    return (result if better else None), usage
