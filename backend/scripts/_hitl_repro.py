import asyncio
import logging
import sys
from pathlib import Path

# Running `python scripts/_hitl_repro.py` puts scripts/ (not backend/) on
# sys.path[0], so `app` isn't importable without this.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import configure_logging, get_settings
from app.core.database import AsyncSessionLocal, engine
from app.guardrails.post_checks import _NEGATIVE_ANSWER_RE
from app.harness.orchestrator import run_query
from app.retrieval.vector_store import VectorStore

configure_logging()
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("httpx2").setLevel(logging.WARNING)

QUERY = "What is human-in-the-loop at Deploy IQ?"


async def main():
    settings = get_settings()
    print(f"groundedness_threshold={settings.groundedness_threshold}")

    async with AsyncSessionLocal() as db:
        vector_store = VectorStore(db)

        for i in range(4):
            stage_timings: dict = {}
            response = await run_query(
                query=QUERY,
                top_k=6,
                filters=None,
                vector_store=vector_store,
                request_id=f"repro-{i}",
                stage_timings=stage_timings,
            )
            neg_match = _NEGATIVE_ANSWER_RE.search(response.answer)
            print(
                f"[run {i}] grounded={response.grounded} confidence={response.confidence:.4f} "
                f"flags={response.guardrail_flags} retrieval_score={stage_timings.get('retrieval_relevance_score')} "
                f"answer_words={len(response.answer.split())} neg_match={neg_match.group(0) if neg_match else None}"
            )
            print("  FULL ANSWER:", response.answer.replace(chr(10), " | "))

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
