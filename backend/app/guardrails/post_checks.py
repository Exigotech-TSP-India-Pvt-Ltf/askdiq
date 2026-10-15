"""Guardrail checks run AFTER generation, before the answer is returned."""

import re

import numpy as np

from app.retrieval.embeddings import current_embedding_stage, embed_texts
from app.schemas.query import SourceChunk

# Answers that explicitly decline to answer (because the corpus genuinely
# lacks the information) are grounded by definition — declining beats
# hallucinating. STEP 7 implements the negative-answer allowance the old
# docstring claimed but never actually checked for.
_NEGATIVE_ANSWER_RE = re.compile(
    r"do(?:es)?n?'?t?\s+(?:provide|contain|specify|include|mention|cover)|"
    r"not\s+(?:yet\s+)?(?:provided|available|specified|claimed|listed|covered|mentioned)|"
    r"no information|"
    r"documents?\s+do(?:es)?\s+not",
    re.IGNORECASE,
)

# Literal fact-anchor check: embeddings capture topic similarity, not whether
# a specific number was actually stated, so a confidently invented $ amount
# or percentage can still pass the semantic check below. Scoped to $/% only
# (not bare numbers) to avoid false positives on word/digit mismatches like
# source "three pillars" vs. answer "3 pillars".
_CURRENCY_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?")
_PERCENT_RE = re.compile(r"\d[\d,]*(?:\.\d+)?\s?%")


def _normalize_figure(figure: str) -> str:
    return re.sub(r"[\s,]", "", figure)


def _unsupported_figures(answer: str, source_text: str) -> list[str]:
    """Return $/% figures in `answer` that don't appear anywhere in the sources."""
    answer_figures = set(_CURRENCY_RE.findall(answer)) | set(
        _PERCENT_RE.findall(answer)
    )
    source_figures = {
        _normalize_figure(f)
        for f in set(_CURRENCY_RE.findall(source_text))
        | set(_PERCENT_RE.findall(source_text))
    }
    return sorted(
        f for f in answer_figures if _normalize_figure(f) not in source_figures
    )


def _cosine_similarities(query: list[float], matrix: list[list[float]]) -> np.ndarray:
    """Cosine similarity of `query` against each row of `matrix` (0.0 for zero vectors)."""
    q = np.asarray(query, dtype=np.float64)
    m = np.asarray(matrix, dtype=np.float64).reshape(len(matrix), -1)
    denom = np.linalg.norm(m, axis=1) * np.linalg.norm(q)
    return np.divide(m @ q, denom, out=np.zeros(len(m)), where=denom != 0)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    return float(_cosine_similarities(a, [b])[0])


def check_has_sources(
    sources: list[SourceChunk],
    min_score: float,
) -> tuple[bool, str | None]:
    """Ensure retrieval returned sufficiently relevant sources."""

    if not sources:
        return False, "no_retrieved_sources"

    if max(s.score for s in sources) < min_score:
        return False, "low_retrieval_confidence"

    return True, None


async def check_groundedness(
    answer: str,
    sources: list[SourceChunk],
    threshold: float,
) -> tuple[bool, float, str | None]:
    """Estimate whether the generated answer is supported by the sources.

    Semantic groundedness (STEP 7): embed the answer and each retrieved
    chunk, then compare via cosine similarity. This replaces the old lexical
    token-overlap heuristic, which falsely rejected short, correctly
    paraphrased answers as `possible_hallucination`.

    Source embeddings are reused from ingestion (via SourceChunk.embedding)
    rather than re-embedded here — chunks are already embedded once at
    ingest time, so re-embedding identical content on every single query
    would just be a wasted API round-trip per source, adding latency for
    no benefit. Only sources missing a precomputed embedding (e.g.
    hand-built in a test) fall back to a fresh embed call.
    """

    if not answer.strip():
        return False, 0.0, "empty_answer"

    if _NEGATIVE_ANSWER_RE.search(answer):
        return True, 1.0, None

    source_text = " ".join(source.content for source in sources)

    if _unsupported_figures(answer, source_text):
        return False, 0.0, "unsupported_numeric_claim"

    try:
        stage_token = current_embedding_stage.set("guardrail")
        try:
            answer_embedding = (await embed_texts([answer]))[0]

            source_embeddings: list[list[float] | None] = [s.embedding for s in sources]
            missing = [i for i, e in enumerate(source_embeddings) if e is None]

            if missing:
                fresh = await embed_texts([sources[i].content for i in missing])
                for index, embedding in zip(missing, fresh, strict=False):
                    source_embeddings[index] = embedding
        finally:
            current_embedding_stage.reset(stage_token)
    except Exception:
        return False, 0.0, "groundedness_check_failed"

    similarities = (
        _cosine_similarities(answer_embedding, source_embeddings)
        if source_embeddings
        else np.zeros(0)
    )

    confidence = float(similarities.max()) if similarities.size else 0.0

    return (
        confidence >= threshold,
        confidence,
        None if confidence >= threshold else "possible_hallucination",
    )


async def run_post_checks(
    answer: str,
    sources: list[SourceChunk],
    groundedness_threshold: float,
    min_retrieval_score: float = 0.3,
) -> tuple[bool, float, list[str]]:
    """Return (grounded, confidence, flags)."""

    flags: list[str] = []

    sources_ok, flag = check_has_sources(
        sources,
        min_retrieval_score,
    )

    if not sources_ok:
        flags.append(flag)
        return False, 0.0, flags

    grounded, confidence, flag = await check_groundedness(
        answer,
        sources,
        groundedness_threshold,
    )

    if not grounded:
        flags.append(flag)

    return grounded, confidence, flags
