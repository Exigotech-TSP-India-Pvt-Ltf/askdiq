"""Page-link requests: "give me the link for the Zero Trust page".

A link ask is detected by bounded regexes on the normalised question. The
page is resolved from document metadata only (``doc_metadata["route"]`` and
its title): the asked topic must be covered by a routed document's route or
title words, never guessed. Its URL is built by
:func:`app.knowledge.links.site_page_url` (SITE_BASE_URL + route) and added
as the fixed ``PAGE_LINK_LINE`` after the verified answer; the model never
writes a URL.

``with_page_link`` wraps :func:`app.harness.orchestrator.run_query` (the one
hook): an answered reply gets the line (and the page's citation); a declined
reply whose page exists becomes the link line alone (the link is a metadata
fact). Without such a page the reply is left unchanged.
"""

from __future__ import annotations

import functools
import inspect
import re
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.guardrails.normalize import normalise_input
from app.harness.citations import TOKEN_DETAILS_KEY, build_citation, citation_records
from app.harness.prompt_catalog import PAGE_LINK_LINE
from app.knowledge.links import site_page_url
from app.models.db_models import Document
from app.retrieval.retriever import route_sources
from app.retrieval.vector_store import VectorStore, quarantined_document_ids
from app.schemas.query import Citation, QueryResponse, SourceChunk

logger = get_logger(__name__)

MAX_QUERY_CHARS = 300
_ANSWERED = "answered"
_APPEND_OUTCOMES = frozenset({_ANSWERED, "conversational"})
# No verified answer (declined, or the answer call failed / came back empty):
# the page link alone is still a metadata fact.
_DECLINED_OUTCOMES = frozenset(
    {"ungrounded", "blocked", "empty_llm_output", "llm_failed"}
)
# A "blocked" reply is taken over only for these flags (never injection,
# identity or regulated-advice refusals).
_DECLINED_FLAGS = frozenset({"off_topic", "not_covered", "possible_hallucination"})
_HOME_ROUTE = "/"
_HOME_WORD = "home"

# --- detection ----------------------------------------------------------------

_LINK_WORD_RE = re.compile(
    r"\b(?:links?|urls?|hyperlinks?|web\s{0,3}pages?|pages?|websites?|"
    r"web\s{1,3}address(?:es)?)\b"
)
_WHERE_READ_RE = re.compile(
    r"\bwhere\s{1,3}(?:can|could|do|should|would)\s{1,3}(?:i|we|you)\s{1,3}"
    r"(?:read|find|see|learn|look|go|get)\b"
)

# --- topic words ----------------------------------------------------------------

# Phrase -> one canonical token; applied to the question and to every page's
# route/title text alike, so both sides compare the same words.
_ALIASES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), token)
    for pattern, token in (
        (r"\bhome\s{0,3}page\b|\bhome\b|\bmain\s{1,3}page\b|\bfront\s{1,3}page\b",
         _HOME_WORD),
        (r"\bzero[\s-]{0,3}trust\b", "zerotrust"),
        (r"\bessential\s{1,3}(?:eight|8)\b|\be8\b", "essentialeight"),
        (r"\biso[\s/-]{0,3}(?:iec[\s/-]{0,3})?27001\b", "iso27001"),
        (r"\biso[\s/-]{0,3}(?:iec[\s/-]{0,3})?42001\b", "iso42001"),
        (r"\b(?:microsoft|office)\s{0,3}365\b|\b[mo]365\b", "microsoft365"),
        (r"\bget(?:ting)?\s{1,3}started\b", "getstarted"),
        (r"\bcontact(?:\s{1,3}us)?\b|\bget\s{1,3}in\s{1,3}touch\b", "contact"),
        (r"\babout\s{1,3}(?:us|deploy\s{0,3}iq|the\s{1,3}company|your\s{1,3}company)"
         r"\b(?!')", "aboutus"),
        (r"\bprivacy(?:\s{1,3}(?:policy|notice|statement))?\b", "privacy"),
        (r"\bterms(?:\s{1,3}(?:and|&)\s{1,3}conditions|\s{1,3}of\s{1,3}"
         r"(?:use|service))?\b|\bt\s{0,2}&\s{0,2}cs?\b|\btos\b", "terms"),
        (r"\bbook(?:ing)?s?(?:\s{1,3}(?:(?:a|an)\s{1,3})?(?:consultation|call|"
         r"meeting|demo|session))?\b|\bconsultations?\b", "booking"),
        (r"\bservices?\b", "services"),
        (r"\bpackages?\b", "packages"),
    )
)  # fmt: skip
_WORD_RE = re.compile(r"[a-z0-9]{1,40}")
# Request scaffolding and brand words: never a page topic.
_STOPWORDS = frozenset(
    {
        "a", "an", "the", "to", "for", "of", "on", "in", "at", "about", "and",
        "or", "with", "from", "me", "my", "i", "we", "us", "you", "your", "our",
        "it", "its", "this", "that", "there", "is", "are", "be", "do", "does",
        "can", "could", "would", "should", "will", "please", "pls", "give",
        "send", "share", "show", "provide", "get", "got", "want", "need", "like",
        "where", "what", "which", "how", "find", "read", "see", "view", "look",
        "learn", "go", "open", "visit", "check", "out", "more", "info",
        "information", "details", "detail", "link", "links", "url", "urls",
        "hyperlink", "hyperlinks", "page", "pages", "webpage", "webpages",
        "web", "website", "websites", "site", "address", "s", "deployiq",
        "deploy", "iq", "exigo", "tech", "have", "has", "any", "direct",
        "official", "relevant", "same", "here", "now", "just", "also",
    }
)  # fmt: skip


@dataclass(frozen=True)
class SitePage:
    """A routed KB document: what a link can point at."""

    document_id: str
    route: str
    title: str


def is_page_link_request(query: str) -> bool:
    """The question asks for a page / link / URL (bounded, linear regexes)."""
    text = normalise_input(query or "")[:MAX_QUERY_CHARS].lower()
    return bool(_LINK_WORD_RE.search(text) or _WHERE_READ_RE.search(text))


def topic_tokens(text: str) -> frozenset[str]:
    """Canonical content words of ``text`` (aliases applied, stopwords dropped)."""
    text = normalise_input(text or "")[:MAX_QUERY_CHARS].lower()
    for pattern, token in _ALIASES:
        text = pattern.sub(f" {token} ", text)
    return frozenset(w for w in _WORD_RE.findall(text) if w not in _STOPWORDS)


def _route_text(route: str) -> str:
    return _HOME_WORD if route == _HOME_ROUTE else route.strip("/").replace("-", " ")


def resolve_page(
    query: str,
    pages: Sequence[SitePage],
    *,
    preferred_ids: Sequence[str] = (),
) -> SitePage | None:
    """The page the question names, from route/title words only.

    Every topic word must appear in the page's route or title words. The page
    whose route words the topic covers best wins; a tie is broken by
    ``preferred_ids`` (cited, then retrieved documents), else no page.
    Without topic words the first preferred routed page is used.
    """
    asked = topic_tokens(query)
    if not asked:
        by_id = {p.document_id: p for p in pages}
        return next((by_id[d] for d in preferred_ids if d in by_id), None)
    ranked: list[tuple[float, SitePage]] = []
    for page in pages:
        route_words = topic_tokens(_route_text(page.route))
        if not asked <= route_words | topic_tokens(page.title):
            continue
        ranked.append((len(asked & route_words) / max(len(route_words), 1), page))
    if not ranked:
        return None
    best = max(score for score, _page in ranked)
    top = [page for score, page in ranked if score == best]
    if len(top) == 1:
        return top[0]
    rank = {doc_id: i for i, doc_id in enumerate(preferred_ids)}
    preferred = sorted(
        (p for p in top if p.document_id in rank), key=lambda p: rank[p.document_id]
    )
    return preferred[0] if preferred else None


# --- data ------------------------------------------------------------------------


async def site_pages(vector_store: VectorStore) -> list[SitePage]:
    """Every non-quarantined document that has a site route."""
    route = Document.doc_metadata["route"].as_string()
    title = Document.doc_metadata["title"].as_string()
    stmt = (
        select(Document.id, route, title)
        .where(route.is_not(None))
        .where(Document.id.not_in(quarantined_document_ids()))
    )
    rows = (await vector_store.session.execute(stmt)).all()
    await vector_store.session.commit()
    return [
        SitePage(str(doc_id), doc_route, doc_title or "")
        for doc_id, doc_route, doc_title in rows
        if isinstance(doc_route, str) and doc_route.startswith("/")
    ]


def _unique(ids: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(ids))


def _page_citation(
    response: QueryResponse, page: SitePage, url: str, chunks: list[SourceChunk]
) -> tuple[list[SourceChunk], list[Citation]]:
    """``response``'s sources/citations, making sure ``page`` is cited."""
    sources = list(response.sources)
    citations = list(response.citations)
    if any(c.document_id == page.document_id for c in citations):
        return sources, citations
    index = next(
        (i for i, s in enumerate(sources) if s.document_id == page.document_id), None
    )
    if index is None:
        sources.append(chunks[0])
        index = len(sources) - 1
    citation = build_citation(index + 1, sources[index]).model_copy(
        update={"url": url, "anchor": None}
    )
    return sources, [*citations, citation]


def _declined(response: QueryResponse) -> bool:
    if response.outcome not in _DECLINED_OUTCOMES:
        return False
    return response.outcome != "blocked" or set(response.guardrail_flags) <= set(
        _DECLINED_FLAGS
    )


async def attach_page_link(  # pylint: disable=too-many-locals
    query: str,
    response: QueryResponse,
    vector_store: VectorStore,
    page: SitePage | None = None,
) -> QueryResponse:
    """``response`` plus the asked page's link line, when the page exists.

    ``page``: already resolved from the question (else resolved here, ties
    broken by the reply's cited and retrieved documents).
    """
    answered = response.outcome in _APPEND_OUTCOMES
    if not (answered or _declined(response)) or not is_page_link_request(query):
        return response
    if page is None:
        preferred = _unique(
            [c.document_id for c in response.citations]
            + [s.document_id for s in response.sources]
        )
        pages = await site_pages(vector_store)
        page = resolve_page(query, pages, preferred_ids=preferred)
    url = site_page_url(get_settings().site_base_url, page.route) if page else None
    chunks = await route_sources(vector_store, page.route) if page and url else []
    if page is None or url is None or not chunks:
        logger.info("page_link_unresolved", outcome=response.outcome)
        return response
    line = PAGE_LINK_LINE.format(url=url)
    details = dict(response.token_details or {})
    details["page_link"] = {"route": page.route, "document_id": page.document_id}
    if answered:
        sources, citations = _page_citation(response, page, url, chunks)
        text = (
            response.answer
            if url in response.answer
            else f"{response.answer}\n\n{line}"
        )
        update: dict[str, Any] = {"answer": text}
    else:
        empty = response.model_copy(update={"sources": [], "citations": []})
        sources, citations = _page_citation(empty, page, url, chunks)
        details["outcome"] = _ANSWERED
        update = {
            "answer": line,
            "outcome": _ANSWERED,
            "grounded": True,
            "confidence": 1.0,
            "guardrail_flags": [],
        }
    details[TOKEN_DETAILS_KEY] = citation_records(citations)
    logger.info("page_link_attached", route=page.route, declined=not answered)
    return response.model_copy(
        update={
            **update,
            "sources": sources,
            "citations": citations,
            "token_details": details,
        }
    )


def with_page_link(
    run: Callable[..., Awaitable[QueryResponse]],
) -> Callable[..., Awaitable[QueryResponse]]:
    """Wrap ``run_query``: its final reply gets the asked page's link line.

    A link ask that names a page is answered as "Tell me about <page>" with
    that page's chunks first and no clarification card (the user already
    named the page), so the short answer comes from the page itself.
    """
    signature = inspect.signature(run)

    @functools.wraps(run)
    async def wrapper(*args: Any, **kwargs: Any) -> QueryResponse:
        bound = signature.bind_partial(*args, **kwargs).arguments
        query, vector_store = bound.get("query"), bound.get("vector_store")
        if not isinstance(query, str) or vector_store is None:
            return await run(*args, **kwargs)
        page = None
        try:
            page = await _asked_page(query, vector_store, bound)
        except SQLAlchemyError:
            logger.exception("page_link_failed")
        if page is not None:
            bound = {
                **bound,
                "query": page_question(page),
                "boost_document_ids": [page.document_id],
                "clarification_reply": True,
            }
        response = await run(**bound)
        try:
            return await attach_page_link(query, response, vector_store, page)
        except SQLAlchemyError:
            logger.exception("page_link_failed")
            return response

    return wrapper


_BRAND_SUFFIX_RE = re.compile(r"\s{0,3}[-|]\s{0,3}deploy\s{0,2}iq\s{0,3}$", re.I)


def page_question(page: SitePage) -> str:
    """The question a page-link ask is answered as (from its title/route)."""
    if page.route == _HOME_ROUTE:
        return "Tell me about DeployIQ"
    label = _BRAND_SUFFIX_RE.sub("", page.title).strip()
    return f"Tell me about {label or _route_text(page.route)}"


async def _asked_page(
    query: str, vector_store: VectorStore, arguments: dict[str, Any]
) -> SitePage | None:
    """The page a fresh link ask names (never for a clarification reply)."""
    if arguments.get("clarification_reply") or not is_page_link_request(query):
        return None
    if not topic_tokens(query):
        return None
    return resolve_page(query, await site_pages(vector_store))
