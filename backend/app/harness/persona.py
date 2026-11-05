"""PERSONA replies: fixed answers to questions about the assistant itself.

Detected deterministically (app.guardrails.conversational.detect_persona or
a persona reply key from the intent classifier); no retrieval, no LLM, no
sources. Identity questions (name, maker, purpose, model, memory, human) are
blocked with the ``identity_probe`` flag as before; capability, language and
chat-privacy questions are conversational. The privacy reply links the
privacy page, built from that page's route (never written by a model).
"""

from __future__ import annotations

import time

from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.guardrails.conversational import persona_reply
from app.harness.page_links import resolve_page, site_pages
from app.harness.prompt_catalog import (
    PERSONA_IDENTITY_INTENTS,
    PERSONA_PRIVACY,
    POLICY_IDENTITY,
    persona_template_id,
)
from app.knowledge.links import document_url
from app.retrieval.vector_store import VectorStore
from app.schemas.query import QueryResponse

logger = get_logger(__name__)

IDENTITY_FLAG = "identity_probe"
_PRIVACY_TOPIC = "privacy policy"


async def privacy_page_url(vector_store: VectorStore | None) -> str | None:
    """The privacy page's URL (SITE_BASE_URL + its route), else None."""
    if vector_store is None:
        return None
    try:
        page = resolve_page(_PRIVACY_TOPIC, await site_pages(vector_store))
    except SQLAlchemyError:
        logger.exception("persona_privacy_page_failed")
        return None
    if page is None:
        return None
    return document_url(None, page.route, get_settings().site_base_url)


async def persona_response(  # pylint: disable=too-many-arguments
    intent: str,
    *,
    vector_store: VectorStore | None,
    request_id: str,
    start: float,
    follow_up_detected: bool,
    history_turns_sent: int,
) -> QueryResponse:
    """The approved persona reply for ``intent`` as a final response."""
    url = await privacy_page_url(vector_store) if intent == PERSONA_PRIVACY else None
    identity = intent in PERSONA_IDENTITY_INTENTS
    outcome = "blocked" if identity else "conversational"
    template_id = persona_template_id(intent)
    details: dict = {"outcome": outcome, "refusal_template": template_id}
    if identity:
        details["policy"] = POLICY_IDENTITY
    latency_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "persona_reply",
        request_id=request_id,
        persona=intent,
        outcome=outcome,
        latency_ms=round(latency_ms, 2),
    )
    return QueryResponse(
        answer=persona_reply(intent, url),
        sources=[],
        citations=[],
        grounded=False,
        confidence=0.0,
        request_id=request_id,
        latency_ms=latency_ms,
        guardrail_flags=[IDENTITY_FLAG] if identity else [],
        follow_up_detected=follow_up_detected,
        history_turns_sent=history_turns_sent,
        outcome=outcome,
        token_details=details,
    )
