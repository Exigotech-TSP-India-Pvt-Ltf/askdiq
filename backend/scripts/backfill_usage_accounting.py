"""One-time idempotent backfill of legacy usage accounting (Task 1.4).

Fills missing `outcome` and computes estimated `cost_usd` (marked is_actual=false)
for legacy assistant chat messages. Dry-run by default, applies changes with --apply.
"""

import argparse
import asyncio
import logging
import sys
import uuid
from pathlib import Path

# Ensure backend directory is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.governance.costing import compute_exchange_cost
from app.governance.outcomes import derive_outcome
from app.models.db_models import ChatMessageRecord

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill")


async def backfill_usage_accounting(
    apply: bool = False,
    batch_size: int = 1000,
    from_id: str | None = None,
    db_url: str | None = None,
) -> dict:
    settings = get_settings()
    url = db_url or settings.database_url
    engine = create_async_engine(url, echo=False)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    cursor_id = uuid.UUID(from_id) if from_id else None
    total_processed = 0
    total_updated = 0
    total_estimated_cost = 0

    mode_label = "APPLY" if apply else "DRY-RUN"
    logger.info(
        "Starting backfill in %s mode (batch_size=%d)...", mode_label, batch_size
    )

    try:
        while True:
            async with session_maker() as session:
                query = (
                    select(ChatMessageRecord)
                    .where(
                        and_(
                            ChatMessageRecord.role == "assistant",
                            ChatMessageRecord.outcome.is_(None),
                        )
                    )
                    .order_by(ChatMessageRecord.created_at, ChatMessageRecord.id)
                    .limit(batch_size)
                )

                results = (await session.execute(query)).scalars().all()
                if not results:
                    break

                for msg in results:
                    total_processed += 1
                    cursor_id = msg.id

                    # 1. Derive canonical outcome
                    outcome = derive_outcome(
                        guardrail_flags=msg.guardrail_flags,
                        grounded=msg.grounded,
                        prompt_tokens=msg.prompt_tokens,
                        completion_tokens=msg.completion_tokens,
                        answer=msg.content,
                        token_details=msg.token_details,
                    )
                    msg.outcome = outcome
                    total_updated += 1

                    # 2. Compute estimated cost if missing
                    if msg.cost_usd is None:
                        total_estimated_cost += 1
                        est_cost, est_details = compute_exchange_cost(
                            prompt_tokens=msg.prompt_tokens or 0,
                            completion_tokens=msg.completion_tokens or 0,
                            reasoning_tokens=msg.reasoning_tokens or 0,
                            cached_tokens=0,
                            query_embed_tokens=msg.embedding_tokens or 0,
                            guardrail_embed_tokens=msg.guardrail_tokens or 0,
                        )
                        msg.cost_usd = est_cost
                        existing_details = msg.token_details or {}
                        msg.token_details = {
                            **est_details,
                            **existing_details,
                            "is_actual": False,
                            "outcome": outcome,
                        }

                if apply:
                    await session.commit()
                    logger.info(
                        "Committed batch: processed=%d (last_id=%s)",
                        total_processed,
                        cursor_id,
                    )
                else:
                    logger.info(
                        "Dry-run batch: evaluated=%d (last_id=%s)",
                        total_processed,
                        cursor_id,
                    )

    finally:
        await engine.dispose()

    summary = {
        "apply": apply,
        "processed": total_processed,
        "updated": total_updated,
        "estimated_costs": total_estimated_cost,
        "last_id": str(cursor_id) if cursor_id else None,
    }
    logger.info("Backfill finished: %s", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Backfill legacy usage accounting and outcomes"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply updates to database (default is dry-run)",
    )
    parser.add_argument(
        "--batch-size", type=int, default=1000, help="Batch size per transaction"
    )
    parser.add_argument(
        "--from-id", type=str, default=None, help="Resume from message UUID"
    )
    parser.add_argument("--db-url", type=str, default=None, help="Database URL")
    args = parser.parse_args()

    asyncio.run(
        backfill_usage_accounting(
            apply=args.apply,
            batch_size=args.batch_size,
            from_id=args.from_id,
            db_url=args.db_url,
        )
    )


if __name__ == "__main__":
    main()
