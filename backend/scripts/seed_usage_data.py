"""Deterministic Usage Data Seeder (Task 0.2).

Generates repeatable, realistic dataset for load testing, integration tests,
and analytics validation with mixed IPs, sessions, outcomes, and audit logs.
"""

import argparse
import asyncio
import random
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

# Ensure backend directory is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.governance.costing import compute_exchange_cost
from app.governance.outcomes import ALL_OUTCOMES
from app.models.db_models import (
    AuditLog,
    ChatMessageRecord,
    ChatSession,
    LatencyRecord,
    MessageFeedback,
    TestIpConfig,
)

TEST_IPS = [
    "127.0.0.1",
    "::1",
    "localhost",
    "10.0.0.42",
    "192.168.1.15",
    "client-dev-mock",
    None,  # missing IP -> categorized as Testing
]

USER_IPS = [
    "203.0.113.19",
    "198.51.100.42",
    "104.28.16.88",
    "151.101.65.140",
    "185.199.108.153",
    "45.33.32.156",
    "8.8.4.4",
]


async def seed_data(messages: int, seed: int = 42, db_url: str | None = None) -> dict:
    settings = get_settings()
    url = db_url or settings.database_url
    engine = create_async_engine(url, echo=False)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    rng = random.Random(seed)
    now = datetime.utcnow()
    start_time = now - timedelta(days=14)

    # Deterministic IP and outcome distributions
    async with session_maker() as session:
        # 1. Ensure test CIDR rule exists
        existing_cidr = (
            await session.execute(
                select(TestIpConfig).where(TestIpConfig.ip_address == "10.0.0.0/8")
            )
        ).scalar_one_or_none()
        if not existing_cidr:
            session.add(
                TestIpConfig(
                    ip_address="10.0.0.0/8",
                    description="Private Subnet Range",
                    is_active=True,
                )
            )
            await session.commit()

        # 2. Generate ChatSessions and Exchanges
        created_sessions = 0
        created_messages = 0
        current_msg_count = 0
        batch_size = 1000

        # Create sessions with turns
        msg_buffer: list[ChatMessageRecord] = []
        latency_buffer: list[LatencyRecord] = []
        feedback_buffer: list[MessageFeedback] = []
        audit_buffer: list[AuditLog] = []

        while current_msg_count < messages:
            is_test_session = rng.random() < 0.35
            ip = rng.choice(TEST_IPS) if is_test_session else rng.choice(USER_IPS)
            turns_in_session = rng.randint(1, 5)
            session_id = uuid.uuid4()
            s_time = start_time + timedelta(
                seconds=rng.randint(0, int(14 * 86400 * 0.9))
            )

            chat_sess = ChatSession(
                id=session_id,
                user_email=f"client-{ip or 'anon'}@askdiq.local",
                title=f"Sample Question Session {created_sessions + 1}",
                created_at=s_time,
                last_activity_at=s_time + timedelta(minutes=turns_in_session * 2),
                expires_at=s_time + timedelta(hours=2),
                created_ip=ip,
            )
            session.add(chat_sess)
            created_sessions += 1

            for turn_idx in range(turns_in_session):
                if current_msg_count >= messages:
                    break

                t_time = s_time + timedelta(minutes=turn_idx * 2)
                request_id = str(uuid.uuid4())
                outcome_choice = rng.choices(
                    ALL_OUTCOMES, weights=[0.60, 0.15, 0.10, 0.10, 0.05]
                )[0]

                # Legacy flag: 10% of rows have outcome=None and cost_usd=None for backfill testing
                is_legacy = rng.random() < 0.10

                prompt_tokens = 0
                completion_tokens = 0
                reasoning_tokens = 0
                cached_tokens = 0
                grounded = True
                guardrail_flags: list[str] = []

                if outcome_choice == "conversational":
                    q_text = "hello assistant"
                    a_text = "Hello! I'm the DeployIQ assistant. Ask me anything."
                    q_embed_tok = 0
                    g_embed_tok = 0
                elif outcome_choice == "blocked":
                    q_text = "off topic or unsafe input"
                    a_text = (
                        "I cannot answer this query as it violates safety guidelines."
                    )
                    guardrail_flags = ["pii_detected"]
                    grounded = False
                    q_embed_tok = 12
                    g_embed_tok = 0
                elif outcome_choice == "llm_failed":
                    q_text = "complex query resulting in timeout"
                    a_text = "Error generating response from LLM."
                    prompt_tokens = rng.randint(400, 800)
                    grounded = False
                    q_embed_tok = 25
                    g_embed_tok = 15
                elif outcome_choice == "ungrounded":
                    q_text = "question with no context in knowledge base"
                    a_text = "I cannot find verifiable facts in the knowledge base."
                    prompt_tokens = rng.randint(300, 600)
                    completion_tokens = rng.randint(50, 150)
                    reasoning_tokens = rng.randint(20, 80)
                    grounded = False
                    q_embed_tok = 30
                    g_embed_tok = 20
                else:  # answered
                    q_text = f"What are Essential Eight controls for scenario {current_msg_count}?"
                    a_text = "Essential Eight consists of eight mitigation strategies to protect Microsoft Windows-based networks."
                    prompt_tokens = rng.randint(350, 900)
                    completion_tokens = rng.randint(80, 250)
                    reasoning_tokens = rng.randint(40, 120)
                    cached_tokens = rng.randint(100, 300) if rng.random() < 0.4 else 0
                    grounded = True
                    q_embed_tok = 35
                    g_embed_tok = 25

                cost_usd, cost_details = compute_exchange_cost(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    reasoning_tokens=reasoning_tokens,
                    cached_tokens=cached_tokens,
                    query_embed_tokens=q_embed_tok,
                    guardrail_embed_tokens=g_embed_tok,
                )

                token_details = {
                    **cost_details,
                    "outcome": outcome_choice,
                    "is_actual": not is_legacy,
                }

                user_msg = ChatMessageRecord(
                    id=uuid.uuid4(),
                    session_id=session_id,
                    turn_index=turn_idx,
                    role="user",
                    content=q_text,
                    created_at=t_time,
                    ip_address=ip,
                )
                asst_msg_id = uuid.uuid4()
                asst_msg = ChatMessageRecord(
                    id=asst_msg_id,
                    session_id=session_id,
                    turn_index=turn_idx,
                    role="assistant",
                    content=a_text,
                    created_at=t_time + timedelta(seconds=2),
                    request_id=request_id,
                    grounded=grounded,
                    guardrail_flags=guardrail_flags,
                    outcome=None if is_legacy else outcome_choice,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=prompt_tokens + completion_tokens,
                    reasoning_tokens=reasoning_tokens,
                    embedding_tokens=q_embed_tok,
                    guardrail_tokens=g_embed_tok,
                    cost_usd=None if is_legacy else cost_usd,
                    token_details=None if is_legacy else token_details,
                )

                msg_buffer.extend([user_msg, asst_msg])

                # Latency metrics record
                lat_rec = LatencyRecord(
                    id=uuid.uuid4(),
                    request_id=request_id,
                    created_at=t_time,
                    total_ms=float(rng.randint(250, 1800)),
                    embedding_ms=float(rng.randint(20, 60)),
                    retrieval_ms=float(rng.randint(30, 90)),
                    guardrail_pre_ms=float(rng.randint(10, 40)),
                    llm_ms=float(rng.randint(200, 1400)),
                    guardrail_post_ms=float(rng.randint(10, 30)),
                    reranker_ms=float(rng.randint(15, 50)),
                )
                latency_buffer.append(lat_rec)

                # Query Audit Log
                audit_rec = AuditLog(
                    id=uuid.uuid4(),
                    request_id=request_id,
                    created_at=t_time,
                    actor=f"client-{ip or 'anon'}@askdiq.local",
                    action="query",
                    ip_address=ip,
                    detail={"outcome": outcome_choice, "cost_usd": cost_usd},
                )
                audit_buffer.append(audit_rec)

                # Optional user feedback (30% of turns)
                if rng.random() < 0.30:
                    fb_rec = MessageFeedback(
                        id=uuid.uuid4(),
                        message_id=asst_msg_id,
                        session_id=session_id,
                        actor=f"client-{ip or 'anon'}",
                        rating=rng.choice([1, 2, 4, 5]),
                        comment=(
                            "Helpful explanation."
                            if rng.random() < 0.5
                            else "Needs more detail."
                        ),
                        created_at=t_time + timedelta(seconds=10),
                    )
                    feedback_buffer.append(fb_rec)

                current_msg_count += 1
                created_messages += 1

                if len(msg_buffer) >= batch_size:
                    session.add_all(msg_buffer)
                    await session.flush()
                    session.add_all(latency_buffer)
                    session.add_all(audit_buffer)
                    session.add_all(feedback_buffer)
                    await session.commit()
                    msg_buffer.clear()
                    latency_buffer.clear()
                    audit_buffer.clear()
                    feedback_buffer.clear()

        # Flush any remaining items
        if msg_buffer:
            session.add_all(msg_buffer)
            await session.flush()
            session.add_all(latency_buffer)
            session.add_all(audit_buffer)
            session.add_all(feedback_buffer)
            await session.commit()

        # Add Ingest Audit Logs for platform overhead tests
        ingest_audit = AuditLog(
            id=uuid.uuid4(),
            request_id=str(uuid.uuid4()),
            created_at=start_time + timedelta(days=1),
            actor="admin@askdiq.local",
            action="ingest",
            ip_address="198.51.100.42",
            detail={"total_chunks": 45, "embedding_tokens": 1250, "cost_usd": 0.000025},
        )
        session.add(ingest_audit)
        await session.commit()

    await engine.dispose()
    return {
        "messages": created_messages,
        "sessions": created_sessions,
        "seed": seed,
    }


def main():
    parser = argparse.ArgumentParser(description="Deterministic usage data seeder")
    parser.add_argument(
        "--messages", type=int, default=1000, help="Number of messages to seed"
    )
    parser.add_argument(
        "--seed", type=int, default=42, help="Random seed for reproducibility"
    )
    parser.add_argument(
        "--db-url", type=str, default=None, help="Database connection URL"
    )
    args = parser.parse_args()

    res = asyncio.run(seed_data(args.messages, args.seed, args.db_url))
    print(
        f"Successfully seeded {res['messages']} messages across {res['sessions']} sessions (seed={res['seed']})"
    )


if __name__ == "__main__":
    main()
