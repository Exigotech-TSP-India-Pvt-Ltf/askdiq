"""Queue insights classification for unlabelled chat history (planchanges4 B6.2.4).

Enqueues ``insights.backfill`` jobs (chunks of message ids); the job worker
labels them under the daily token budget. Nothing calls the LLM inline.

Usage (from ``backend/``)::

    venv\\Scripts\\python.exe scripts/backfill_turn_insights.py --limit 500 --dry-run
    venv\\Scripts\\python.exe scripts/backfill_turn_insights.py --since 2026-07-01
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.insights.jobs import MAX_BACKFILL_CHUNK, enqueue_backfill
from app.insights.repository import InsightsRepository

MAX_LIMIT = 100_000
DEFAULT_CHUNK = 100


def _since(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed  # chat_messages.created_at is naive UTC


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--limit",
        type=int,
        default=1000,
        help=f"Max exchanges to queue (1..{MAX_LIMIT})",
    )
    parser.add_argument(
        "--since", type=_since, default=None, help="ISO date/time (UTC if naive)"
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK,
        help=f"Message ids per backfill job (1..{MAX_BACKFILL_CHUNK})",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print counts; write nothing"
    )
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= MAX_LIMIT:
        parser.error(f"--limit must be between 1 and {MAX_LIMIT}")
    if not 1 <= args.chunk_size <= MAX_BACKFILL_CHUNK:
        parser.error(f"--chunk-size must be between 1 and {MAX_BACKFILL_CHUNK}")
    return args


async def run(
    args: argparse.Namespace,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    engine: AsyncEngine | None = None,
) -> dict[str, int | bool]:
    if session_factory is None:
        from app.core.database import AsyncSessionLocal

        session_factory = AsyncSessionLocal
    async with session_factory() as session:
        repo = InsightsRepository(session)
        pending = await repo.count_pending(since=args.since)
        ids = await repo.pending_ids(since=args.since, limit=args.limit)

    chunks = [ids[i : i + args.chunk_size] for i in range(0, len(ids), args.chunk_size)]
    summary: dict[str, int | bool] = {
        "pending": pending,
        "selected": len(ids),
        "jobs": len(chunks),
        "enqueued": 0,
        "dry_run": args.dry_run,
    }
    if not args.dry_run:
        for chunk in chunks:
            if await enqueue_backfill(chunk, engine=engine) is not None:
                summary["enqueued"] = int(summary["enqueued"]) + 1
    return summary


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    summary = asyncio.run(run(args))
    mode = "DRY RUN (nothing written)" if summary["dry_run"] else "queued"
    print(
        f"{mode}: pending={summary['pending']} selected={summary['selected']} "
        f"jobs={summary['jobs']} enqueued={summary['enqueued']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
