"""Page-link requests: "give me the link for the Zero Trust page".

A link ask is detected by bounded regexes on the normalised question. The
page is resolved from document metadata only (``doc_metadata["route"]`` and
its title): the asked topic must be covered by a routed document's route or
title words, never guessed. Its URL is built by
:func:`app.knowledge.links.site_page_url` (SITE_BASE_URL + route) and added
as the fixed ``PAGE_LINK_LINE`` after the verified answer; the model never
writes a URL.

``with_page_link`` wraps :func:`app.harness.orchestrator.run_query` (the one
hook). A pure link ask (every topic word names one page) is answered without
the model: one sentence of the page's own copy (``PAGE_SUMMARY``) plus the
link line, citing that page. A link ask inside a longer question keeps the
full answer path: an answered reply gets the line (and the page's citation);
a declined reply whose page exists becomes the link line alone (the link is
a metadata fact). Without such a page the reply is left unchanged.
"""

from __future__ import annotations

import functools
import inspect
import re
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.guardrails.normalize import normalise_input
from app.harness.citations import TOKEN_DETAILS_KEY, build_citation, citation_records
from app.harness.prompt_catalog import (
    PAGE_LINK_LINE,
    PAGE_LINK_TEMPLATE_ID,
    PAGE_SUMMARY,
)
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
    description: str = ""


def _is_link_text(text: str) -> bool:
    return bool(_LINK_WORD_RE.search(text) or _WHERE_READ_RE.search(text))


def is_page_link_request(query: str) -> bool:
    """The question asks for a page / link / URL (bounded, linear regexes)."""
    return _is_link_text(normalise_input(query or "")[:MAX_QUERY_CHARS].lower())


def _canonical(text: str) -> str:
    """Normalised, lower-cased ``text`` with every alias phrase as its token."""
    text = normalise_input(text or "")[:MAX_QUERY_CHARS].lower()
    for pattern, token in _ALIASES:
        text = pattern.sub(f" {token} ", text)
    return text


def topic_tokens(text: str) -> frozenset[str]:
    """Canonical content words of ``text`` (aliases applied, stopwords dropped)."""
    return frozenset(
        w for w in _WORD_RE.findall(_canonical(text)) if w not in _STOPWORDS
    )


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
    description = Document.doc_metadata["description"].as_string()
    stmt = (
        select(Document.id, route, title, description)
        .where(route.is_not(None))
        .where(Document.id.not_in(quarantined_document_ids()))
    )
    rows = (await vector_store.session.execute(stmt)).all()
    await vector_store.session.commit()
    return [
        SitePage(str(doc_id), doc_route, doc_title or "", doc_description or "")
        for doc_id, doc_route, doc_title, doc_description in rows
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
    """Wrap ``run_query``: a link ask gets the asked page's link line.

    A pure link ask (the whole question names one page) is answered without
    ``run`` by :func:`page_link_reply`; if that page has no chunks it is
    answered as "Tell me about <page>" with that page's chunks first and no
    clarification card. A link ask inside a longer question runs as asked
    and gets the named page's line after the answer.
    """
    signature = inspect.signature(run)

    @functools.wraps(run)
    async def wrapper(*args: Any, **kwargs: Any) -> QueryResponse:
        started = time.perf_counter()
        bound = signature.bind_partial(*args, **kwargs).arguments
        query, vector_store = bound.get("query"), bound.get("vector_store")
        if not isinstance(query, str) or vector_store is None:
            return await run(*args, **kwargs)
        target = _LinkTarget(None, combined=False)
        try:
            target = await _link_target(query, vector_store, bound)
            if target.page is not None and not target.combined:
                reply = await page_link_reply(
                    target.page,
                    vector_store,
                    request_id=str(bound.get("request_id") or ""),
                    started=started,
                )
                if reply is not None:
                    return reply
        except SQLAlchemyError:
            logger.exception("page_link_failed")
        page = target.page
        if page is not None and not target.combined:
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


@dataclass(frozen=True)
class _LinkTarget:
    page: SitePage | None
    combined: bool  # the link ask is one clause of a longer question


# Clause breaks of a longer question (applied to the canonical text, so
# "terms and conditions" is already one token).
_CLAUSE_RE = re.compile(r"[?!.;,]|\b(?:and|also|plus|then|but)\b")


def _clause_page(query: str, pages: Sequence[SitePage]) -> SitePage | None:
    """The one page the link clauses of a longer question name, else None."""
    found: dict[str, SitePage] = {}
    for clause in _CLAUSE_RE.split(_canonical(query)):
        if not (_is_link_text(clause) and topic_tokens(clause)):
            continue
        page = resolve_page(clause, pages)
        if page is not None:
            found[page.document_id] = page
    return next(iter(found.values())) if len(found) == 1 else None


async def _link_target(
    query: str, vector_store: VectorStore, arguments: dict[str, Any]
) -> _LinkTarget:
    """The page a fresh link ask names (never for a clarification reply)."""
    none = _LinkTarget(None, combined=False)
    if arguments.get("clarification_reply") or not is_page_link_request(query):
        return none
    if not topic_tokens(query):
        return none
    pages = await site_pages(vector_store)
    page = resolve_page(query, pages)
    # Text past the scanned prefix is never checked: not a pure link ask.
    if page is not None and len(normalise_input(query)) <= MAX_QUERY_CHARS:
        return _LinkTarget(page, combined=False)
    return _LinkTarget(_clause_page(query, pages), combined=True)


# --- the model-free reply ---------------------------------------------------------

MAX_LINK_REPLY_WORDS = 40
_PROSE_MIN_WORDS = 8
_PROSE_SCAN_CHARS = 4000
_LABEL_MAX_WORDS = 4
_ING = "ing"
# First words of call-to-action copy ("Assess and harden ...").
_ACTION_VERBS = frozenset(
    {
        "achieve", "assess", "book", "browse", "build", "check", "compare",
        "connect", "contact", "discover", "discuss", "download", "explore",
        "find", "get", "harden", "implement", "join", "learn", "manage",
        "meet", "plan", "protect", "read", "request", "review", "schedule",
        "secure", "see", "start", "strengthen", "talk", "understand", "view",
    }
)  # fmt: skip
_TOPIC_LEADS = frozenset({"a", "an", "the", "how", "what", "why", "who", "when"})
# A verb among the first words: the copy is a whole sentence ("DeployIQ is
# ..."), quoted as is; other copy is a noun phrase the "covers" frame takes.
_FINITE_VERBS = frozenset(
    {
        "is", "are", "was", "were", "has", "have", "uses", "helps", "gives",
        "offers", "provides", "delivers", "lets", "makes", "brings", "keeps",
        "works", "means", "includes", "combines", "connects", "supports",
    }
)  # fmt: skip
_VERB_WINDOW = 6
_MARKER_WORDS = 1  # the " [1]" after the sentence
_SENTENCE_END = ".!?"
_ELLIPSIS = "…"
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_LABEL_CUT_RE = re.compile(r"\s(?:for|in)\s")
_OUR_RE = re.compile(r"^our\s+", re.I)
_LETTERS_RE = re.compile(r"[^a-z]")
_MD_LINK_RE = re.compile(r"\[([^\]\n]{1,200})\]\([^)\s]{1,500}\)")
_NOT_PROSE_START = frozenset("-*+|>#!_(`[")


def _words(text: str) -> int:
    return len(text.split())


def page_label(page: SitePage) -> str:
    """How a reply names the page ("Zero Trust Services", "home", "Vision")."""
    if page.route == _HOME_ROUTE:
        return _HOME_WORD
    label = _BRAND_SUFFIX_RE.sub("", page.title).strip()
    if _words(label) > _LABEL_MAX_WORDS:
        label = _LABEL_CUT_RE.split(label, maxsplit=1)[0]
    label = label or _route_text(page.route).title()
    return _OUR_RE.sub("", label) or label


def _first_prose_sentence(text: str) -> str | None:
    """The first real sentence of page text (no headings, bullets or notes)."""
    for raw in text[:_PROSE_SCAN_CHARS].split("\n"):
        line = _MD_LINK_RE.sub(r"\1", raw).replace("**", "").strip()
        if not line or line[0] in _NOT_PROSE_START or line[0].isdigit():
            continue
        sentence = _SENTENCE_SPLIT_RE.split(line, maxsplit=1)[0]
        if _words(sentence) >= _PROSE_MIN_WORDS and sentence[-1] in _SENTENCE_END:
            return sentence
    return None


def _fit(text: str, budget: int) -> str:
    """Whole leading sentences of ``text`` within ``budget`` words; a first
    sentence that is too long loses trailing ", ..." clauses (else words)."""
    sentences = _SENTENCE_SPLIT_RE.split(text.strip())
    kept: list[str] = []
    for sentence in sentences:
        if _words(" ".join([*kept, sentence])) > budget:
            break
        kept.append(sentence)
    if kept:
        return " ".join(kept)
    first = sentences[0].rstrip(_SENTENCE_END + " ")
    while _words(first) > budget and ", " in first:
        first = first.rsplit(", ", 1)[0]
    if _words(first) > budget:
        return " ".join(first.split()[:budget]).rstrip(",;:-") + _ELLIPSIS
    return first + "."


def _lower_first(text: str) -> str:
    """``text`` with its first letter lowered, unless that word is an acronym
    or a brand ("ISO", "DeployIQ")."""
    word = text.split(maxsplit=1)[0] if text.strip() else ""
    lower = word[1:].islower() or (len(word) == 1 and word != "I")
    return text[0].lower() + text[1:] if lower else text


def _summary_kind(copy: str) -> str:
    """Which ``PAGE_SUMMARY`` frame fits how ``copy`` starts."""
    words = [_LETTERS_RE.sub("", w.lower()) for w in copy.split()[:_VERB_WINDOW]]
    lead = words[0] if words else ""
    if lead in _ACTION_VERBS:
        return "action"
    if lead in _TOPIC_LEADS or (len(lead) > len(_ING) and lead.endswith(_ING)):
        return "topic"
    return "quote" if _FINITE_VERBS.intersection(words) else "topic"


def page_summary(page: SitePage, copy: str, link_line: str) -> str:
    """One short sentence of ``copy`` (the page's own words) framed as a reply,
    within ``MAX_LINK_REPLY_WORDS`` together with ``link_line``."""
    copy = " ".join(copy.split())
    kind = _summary_kind(copy)
    frame, label = PAGE_SUMMARY[kind], page_label(page)
    budget = (
        MAX_LINK_REPLY_WORDS
        - _words(frame.format(label=label, summary=""))
        - _MARKER_WORDS
        - _words(link_line)
    )
    summary = _fit(copy, max(budget, _PROSE_MIN_WORDS))
    if kind != "quote":
        summary = _lower_first(summary)
    if summary[-1] not in _SENTENCE_END + _ELLIPSIS:
        summary += "."
    return frame.format(label=label, summary=summary)


def _summary_copy(
    page: SitePage, chunks: Sequence[SourceChunk]
) -> tuple[str | None, SourceChunk]:
    """The page copy a reply quotes and the chunk it cites."""
    if page.description.strip():
        return page.description, chunks[0]
    for chunk in chunks:
        sentence = _first_prose_sentence(chunk.content)
        if sentence:
            return sentence, chunk
    return None, chunks[0]


async def page_link_reply(
    page: SitePage,
    vector_store: VectorStore,
    *,
    request_id: str,
    started: float,
) -> QueryResponse | None:
    """A pure link ask answered without the model: one sentence of the page's
    own copy, citing that page, plus the link line. None when the page has no
    URL or no chunks (the caller falls back to the answer path)."""
    url = site_page_url(get_settings().site_base_url, page.route)
    chunks = await route_sources(vector_store, page.route) if url else []
    if url is None or not chunks:
        return None
    line = PAGE_LINK_LINE.format(url=url)
    copy, source = _summary_copy(page, chunks)
    answer = f"{page_summary(page, copy, line)} [1] {line}" if copy else line
    citation = build_citation(1, source).model_copy(update={"url": url, "anchor": None})
    details: dict[str, Any] = {
        "outcome": _ANSWERED,
        "refusal_template": PAGE_LINK_TEMPLATE_ID,
        "page_link": {
            "route": page.route,
            "document_id": page.document_id,
            "copy": "description" if page.description.strip() else "page_text",
        },
        TOKEN_DETAILS_KEY: citation_records([citation]),
    }
    latency_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "page_link_reply",
        request_id=request_id,
        route=page.route,
        latency_ms=round(latency_ms, 2),
    )
    return QueryResponse(
        answer=answer,
        sources=[source],
        citations=[citation],
        grounded=True,
        confidence=1.0,
        request_id=request_id,
        latency_ms=latency_ms,
        guardrail_flags=[],
        outcome=_ANSWERED,
        token_details=details,
    )
