"""Retrieval-only recall benchmark over the golden cases (embeddings only, no LLM).

Mirrors the retrieval step of app.harness.orchestrator.run_query for a
single-turn query (no history, no clarification reply): retrieve_parts ->
decide_route -> covered_sources -> with_section_siblings. A case is a hit when
a final source's document route is one of the case's expected routes.

Usage:
    venv\\Scripts\\python.exe scripts/retrieval_recall_bench.py --out recall.json
    venv\\Scripts\\python.exe scripts/retrieval_recall_bench.py --limit 60 --out r.json
    venv\\Scripts\\python.exe scripts/retrieval_recall_bench.py --ids id1,id2 --out r.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# pylint: disable=wrong-import-position
from live_golden_eval import has_term, norm  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.database import AsyncSessionLocal  # noqa: E402
from app.harness.clarify import clarify_allowed, is_buying  # noqa: E402
from app.harness.orchestrator import (  # noqa: E402
    _PRICE_ASK_RE,
    _SALES_ASK_RE,
    is_booking_info_question,
)
from app.models.db_models import Document  # noqa: E402
from app.retrieval.retriever import (  # noqa: E402
    Route,
    covered_sources,
    decide_route,
    decompose_query,
    resolve_page_urls,
    retrieve_parts,
    with_section_siblings,
)
from app.retrieval.vector_store import VectorStore  # noqa: E402

GOLDEN_DIR = ROOT / "tests" / "golden"
GOLDEN_FILES = (
    "golden_cases.json",
    "sections_a.json",
    "sections_b.json",
    "final_misc.json",
)
EXPECTS = {"answer", "partial"}
CONCURRENCY = 4


def _norm_route(route: str | None) -> str | None:
    if route is None:
        return None
    route = str(route).strip()
    return "/" + route.strip("/") if route else None


def load_cases(ids: set[str] | None, limit: int | None) -> list[dict]:
    cases: list[dict] = []
    seen: set[str] = set()
    for name in GOLDEN_FILES:
        data = json.loads((GOLDEN_DIR / name).read_text(encoding="utf-8"))
        for case in data.get("cases", []):
            turns = case.get("turns") or []
            if len(turns) != 1:
                continue
            turn = turns[0]
            routes = [r for r in (turn.get("routes") or []) if r]
            if turn.get("expect") not in EXPECTS or not routes:
                continue
            if case["id"] in seen or (ids and case["id"] not in ids):
                continue
            seen.add(case["id"])
            cases.append(
                {
                    "id": case["id"],
                    "category": case.get("category", "?"),
                    "file": name,
                    "query": turn["query"],
                    "expect": turn["expect"],
                    "routes": sorted({_norm_route(r) for r in routes}),
                    "must_contain": turn.get("must_contain") or [],
                }
            )
    return cases[:limit] if limit else cases


async def document_routes() -> dict[str, str | None]:
    async with AsyncSessionLocal() as session:
        rows = await session.execute(select(Document.id, Document.doc_metadata))
        return {
            str(doc_id): _norm_route((meta or {}).get("route")) for doc_id, meta in rows
        }


def _buying(query: str) -> bool:
    """The orchestrator's heuristic buying flag (classifier-free fallback)."""
    asks = decompose_query(query)
    return (
        (is_buying(query) and not is_booking_info_question(query))
        or _SALES_ASK_RE.search(query) is not None
        or (
            len(asks) > 1
            and any(
                _PRICE_ASK_RE.search(a) is not None and not is_booking_info_question(a)
                for a in asks
            )
        )
    )


async def final_sources(vector_store: VectorStore, query: str, top_k: int) -> list:
    settings = get_settings()
    query = resolve_page_urls(query)
    buying = _buying(query)
    asks = len(decompose_query(query))
    wide = clarify_allowed(query, None, clarification_reply=False, parts=asks)
    retrieval = await retrieve_parts(
        vector_store,
        query,
        top_k=max(top_k, settings.clarify_retrieval_top_k) if wide else top_k,
        filters=None,
        history=None,
        max_documents=settings.clarify_retrieval_max_documents if wide else None,
        split=True,
        buying=buying,
        prefer_documents=None,
    )
    decision = decide_route(retrieval.parts, buying=buying)
    sources = covered_sources(retrieval, decision)
    if decision.route in (Route.ANSWER, Route.PARTIAL):
        sources = await with_section_siblings(vector_store, sources)
    return sources


def _source_route(source, routes_by_doc: dict[str, str | None]) -> str | None:
    meta = source.metadata or {}
    return _norm_route(meta.get("route")) or routes_by_doc.get(str(source.document_id))


async def run_case(
    case: dict,
    sem: asyncio.Semaphore,
    routes_by_doc: dict[str, str | None],
    top_k: int,
) -> dict:
    async with sem, AsyncSessionLocal() as session:
        t0 = time.perf_counter()
        try:
            sources = await final_sources(VectorStore(session), case["query"], top_k)
            error = None
        except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-except
            sources, error = [], f"{type(exc).__name__}: {exc}"
        ms = (time.perf_counter() - t0) * 1000
    got = [_source_route(s, routes_by_doc) for s in sources]
    expected = set(case["routes"])
    text_k = norm(" ".join(s.content for s in sources[:top_k]))
    missing = [t for t in case["must_contain"] if not has_term(text_k, t)]
    return {
        **case,
        "ms": round(ms, 1),
        "hit_at_k": any(r in expected for r in got[:top_k]),
        "hit_any": any(r in expected for r in got),
        "got_routes": list(dict.fromkeys(r for r in got if r)),
        "n_sources": len(sources),
        "content_hit_at_k": (not missing) if case["must_contain"] else None,
        "content_missing": missing,
        "error": error,
    }


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(q * (len(ordered) - 1) + 0.5))], 1)


def summarize(results: list[dict], top_k: int, wall_s: float) -> dict:
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_cat[r["category"]].append(r)
    n = len(results) or 1
    latencies = [r["ms"] for r in results]
    content = [r for r in results if r["content_hit_at_k"] is not None]
    content_by_cat: dict[str, list[dict]] = defaultdict(list)
    for r in content:
        content_by_cat[r["category"]].append(r)
    return {
        "top_k": top_k,
        "cases": len(results),
        f"recall_at_{top_k}": round(sum(r["hit_at_k"] for r in results) / n, 4),
        "recall_any_source": round(sum(r["hit_any"] for r in results) / n, 4),
        "per_category": {
            cat: {
                "n": len(rs),
                f"recall_at_{top_k}": round(
                    sum(r["hit_at_k"] for r in rs) / len(rs), 4
                ),
            }
            for cat, rs in sorted(by_cat.items())
        },
        "missed_ids": [r["id"] for r in results if not r["hit_at_k"]],
        "content_cases": len(content),
        f"content_hit_at_{top_k}": round(
            sum(r["content_hit_at_k"] for r in content) / (len(content) or 1), 4
        ),
        "content_per_category": {
            cat: {
                "n": len(rs),
                f"content_hit_at_{top_k}": round(
                    sum(r["content_hit_at_k"] for r in rs) / len(rs), 4
                ),
            }
            for cat, rs in sorted(content_by_cat.items())
        },
        "content_missed_ids": [r["id"] for r in content if not r["content_hit_at_k"]],
        "errors": [r["id"] for r in results if r["error"]],
        "latency_ms": {
            "p50": round(statistics.median(latencies), 1) if latencies else 0.0,
            "p95": _pct(latencies, 0.95),
        },
        "wall_s": round(wall_s, 1),
        "results": results,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--ids", default=None, help="comma-separated case ids")
    args = parser.parse_args()

    ids = {i.strip() for i in args.ids.split(",") if i.strip()} if args.ids else None
    cases = load_cases(ids, args.limit)
    top_k = get_settings().default_top_k
    start = time.perf_counter()
    routes_by_doc = await document_routes()
    sem = asyncio.Semaphore(CONCURRENCY)
    results = await asyncio.gather(
        *(run_case(c, sem, routes_by_doc, top_k) for c in cases)
    )
    report = summarize(list(results), top_k, time.perf_counter() - start)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    summary = {
        k: v
        for k, v in report.items()
        if k not in ("results", "per_category", "content_per_category")
    }
    sys.stdout.write(json.dumps(summary, indent=2) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
