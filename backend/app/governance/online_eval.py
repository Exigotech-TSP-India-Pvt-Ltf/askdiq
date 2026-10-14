"""Online async quality sampler (continuous production evaluation).

Asynchronously samples and grades live query traffic in the background to detect:
- Leaked credentials or secrets (SECRET_PATTERNS)
- PII disclosure in answers (pii_leaked)
- Hallucinations / ungrounded claims via lightweight LLM judge sampling

Runs as a non-blocking background task with bounded concurrency and automatic
skip if TPM headroom is depleted, ensuring user-facing latency is never impacted.
"""

import asyncio
import logging
import random
import threading
from collections import deque
from typing import Any

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.quota import quota_monitor
from app.evaluation.judge import judge_answer
from app.evaluation.quality import SECRET_PATTERNS, pii_leaked
from app.models.db_models import AuditLog

logger = logging.getLogger(__name__)

# Retain strong references to background evaluation tasks to avoid GC termination
_background_tasks: set[asyncio.Task] = set()
_eval_semaphore = asyncio.Semaphore(4)


class OnlineQualityTracker:
    def __init__(self, max_history: int = 1000) -> None:
        self.sampled_count: int = 0
        self.violation_count: int = 0
        self.secret_leak_count: int = 0
        self.pii_leak_count: int = 0
        self.unfaithful_count: int = 0
        self.faithfulness_history: deque[int] = deque(maxlen=max_history)
        self._lock = threading.Lock()

    def record(
        self,
        flags: list[str],
        faithfulness: int | None = None,
    ) -> None:
        with self._lock:
            self.sampled_count += 1
            if flags:
                self.violation_count += 1
            if "secret_leak_detected" in flags:
                self.secret_leak_count += 1
            if "pii_leak_detected" in flags:
                self.pii_leak_count += 1
            if "online_unfaithful" in flags:
                self.unfaithful_count += 1
            if faithfulness is not None:
                self.faithfulness_history.append(faithfulness)

    def get_kpis(self) -> dict[str, Any]:
        with self._lock:
            total = self.sampled_count
            v_rate = round(self.violation_count / total, 4) if total > 0 else 0.0
            avg_faith = (
                round(
                    sum(self.faithfulness_history) / len(self.faithfulness_history), 2
                )
                if self.faithfulness_history
                else 5.0
            )
            return {
                "sampled_count": total,
                "violation_count": self.violation_count,
                "violation_rate": v_rate,
                "mean_faithfulness": avg_faith,
                "secret_leak_count": self.secret_leak_count,
                "pii_leak_count": self.pii_leak_count,
                "unfaithful_count": self.unfaithful_count,
            }


quality_tracker = OnlineQualityTracker()


async def _run_evaluation_task(
    request_id: str,
    query: str,
    answer: str,
    context_chunks: list[str],
    initial_flags: list[str],
    ip_address: str,
    force_llm_judge: bool = False,
) -> None:
    async with _eval_semaphore:
        settings = get_settings()
        detected_flags: list[str] = list(initial_flags)

        # 1. Secret Scanner (Lightweight regex rules)
        for rule_id, pattern in SECRET_PATTERNS.items():
            if pattern.search(answer):
                detected_flags.append("secret_leak_detected")
                logger.warning(
                    "online_eval_rule_violation request_id=%s rule_matched=%s",
                    request_id,
                    rule_id,
                )
                break

        # 2. PII Leakage Scanner
        leaked = pii_leaked(answer, query)
        if leaked:
            detected_flags.append("pii_leak_detected")
            logger.warning(
                "online_eval_pii_leak request_id=%s types=%s",
                request_id,
                leaked,
            )

        # 3. Sampled LLM Judge
        faithfulness_score: int | None = None
        judge_scores_dict: dict[str, Any] | None = None

        quota_sum = quota_monitor.get_summary()
        chat_headroom = quota_sum["chat"]["tpm_headroom_pct"]

        # Only execute LLM judge if online eval is enabled (or forced), and TPM headroom is >= 15%
        should_run_judge = (
            (settings.online_eval_enabled or force_llm_judge)
            and chat_headroom >= 15.0
            and bool(context_chunks)
            and bool(answer.strip())
        )

        if should_run_judge:
            try:
                scores = await judge_answer(
                    question=query,
                    context_chunks=context_chunks,
                    answer=answer,
                )
                if scores:
                    judge_scores_dict = scores.as_dict()
                    faithfulness_score = scores.faithfulness
                    if scores.faithfulness <= 2:
                        detected_flags.append("online_unfaithful")
                        logger.warning(
                            "online_eval_unfaithful request_id=%s faithfulness=%d reason=%s",
                            request_id,
                            scores.faithfulness,
                            scores.reason,
                        )
            except Exception as exc:
                logger.warning(
                    "online_eval_judge_error request_id=%s err=%s", request_id, exc
                )

        # Record rolling metrics
        unique_flags = list(dict.fromkeys(detected_flags))
        quality_tracker.record(unique_flags, faithfulness=faithfulness_score)

        # 4. Insert AuditLog on violation
        violation_flags = [
            f
            for f in unique_flags
            if f in {"secret_leak_detected", "pii_leak_detected", "online_unfaithful"}
        ]
        if violation_flags:
            try:
                async with AsyncSessionLocal() as session:
                    log_entry = AuditLog(
                        action="online_eval",
                        actor="system:online_eval",
                        request_id=request_id,
                        ip_address=ip_address or "127.0.0.1",
                        detail={
                            "guardrail_flags": violation_flags,
                            "query_len": len(query),
                            "answer_len": len(answer),
                            "judge_scores": judge_scores_dict,
                        },
                    )
                    session.add(log_entry)
                    await session.commit()
            except Exception as exc:
                logger.error(
                    "failed_to_write_online_eval_audit request_id=%s err=%s",
                    request_id,
                    exc,
                )


def schedule_online_evaluation(
    request_id: str,
    query: str,
    answer: str,
    sources: list[Any] | None = None,
    guardrail_flags: list[str] | None = None,
    ip_address: str = "",
    force: bool = False,
) -> None:
    """Schedule asynchronous quality evaluation of an exchange.

    Triggers:
    - 100% of exchanges that have guardrail flags (cheap rules only)
    - Configured random sample (Settings.online_eval_sample_rate)
    """
    settings = get_settings()
    has_flags = bool(guardrail_flags)
    sample_rate = settings.online_eval_sample_rate

    is_sampled = force or has_flags or (random.random() < sample_rate)  # nosec B311
    if not is_sampled and not settings.online_eval_enabled:
        return

    # Extract plain text content from sources
    context_chunks: list[str] = []
    if sources:
        for s in sources:
            if hasattr(s, "content"):
                context_chunks.append(str(s.content))
            elif isinstance(s, dict):
                context_chunks.append(str(s.get("content", "")))
            elif isinstance(s, str):
                context_chunks.append(s)

    task = asyncio.create_task(
        _run_evaluation_task(
            request_id=request_id,
            query=query,
            answer=answer,
            context_chunks=context_chunks,
            initial_flags=guardrail_flags or [],
            ip_address=ip_address,
            force_llm_judge=force,
        )
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
