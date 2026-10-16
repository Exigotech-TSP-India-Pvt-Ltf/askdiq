"""Evaluation (Live) chart additions (B7.4): quality dimensions of the current
vs previous run and the pass-rate trend, for ``GET /metrics/evaluation/live``.

What a "run" is depends on the view the endpoint already serves:

- ``testing`` / ``overview`` show the benchmark golden suite, so a run is one
  evaluation report file written by ``scripts/run_evaluation.py --json``
  (``run_id`` = file name without ``.json``).
- ``users`` shows continuous online evaluation, so a run is one UTC day of
  ``evaluation_records`` (``run_id`` = ``live-YYYY-MM-DD``).

Only measured values are returned: a dimension a run did not measure is null.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Float, case, cast, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging_config import get_logger
from app.dashboard.window import iso_z, to_naive_utc
from app.models.db_models import EvaluationRecord

logger = get_logger(__name__)

MAX_RUNS = 30
JUDGE_SCALE = 5.0
LIVE_PREFIX = "live-"
REPORT_PATTERNS = ("eval*report*.json", "evaluation_report*.json")

DIMENSIONS: tuple[tuple[str, str], ...] = (
    ("retrieval_recall", "Retrieval recall"),
    ("groundedness", "Groundedness"),
    ("faithfulness", "Faithfulness"),
    ("citation_precision", "Citation precision"),
    ("safety", "Safety"),
    ("pass_rate", "Pass rate"),
)


@dataclass(frozen=True, slots=True)
class Run:
    run_id: str
    started_at: datetime
    n_cases: int
    pass_rate: float
    scores: Mapping[str, float | None] = field(default_factory=dict)

    def ref(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "started_at": iso_z(self.started_at),
            "n_cases": self.n_cases,
        }


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _nested(report: Mapping[str, Any], *keys: str) -> Any:
    node: Any = report
    for key in keys:
        if not isinstance(node, Mapping):
            return None
        node = node.get(key)
    return node


def _started_at(report: Mapping[str, Any], fallback: datetime) -> datetime:
    for key in ("started_at", "timestamp", "generated_at"):
        raw = report.get(key)
        if isinstance(raw, str):
            try:
                return to_naive_utc(datetime.fromisoformat(raw.replace("Z", "+00:00")))
            except ValueError:
                continue
    return fallback


def run_from_report(
    run_id: str, report: Mapping[str, Any], fallback: datetime
) -> Run | None:
    """A benchmark run from a report dict; None when it has no pass rate."""
    pass_rate = _number(report.get("pass_rate"))
    if pass_rate is None:
        return None
    cases = report.get("cases")
    n_cases = _number(report.get("n_cases"))
    if n_cases is None:
        n_cases = float(len(cases)) if isinstance(cases, list) else 0.0
    faithfulness = _number(_nested(report, "judge", "faithfulness"))
    scores = {
        "retrieval_recall": _number(report.get("retrieval_recall")),
        "groundedness": _number(report.get("groundedness_accuracy")),
        "faithfulness": faithfulness / JUDGE_SCALE
        if faithfulness is not None
        else None,
        "citation_precision": _number(_nested(report, "citation", "precision")),
        "safety": _number(_nested(report, "safety", "passed")),
        "pass_rate": pass_rate,
    }
    return Run(run_id, _started_at(report, fallback), int(n_cases), pass_rate, scores)


def _report_files(dirs: Iterable[Path]) -> list[Path]:
    files: set[Path] = set()
    for directory in dirs:
        if directory.is_dir():
            for pattern in REPORT_PATTERNS:
                files.update(directory.glob(pattern))
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[:MAX_RUNS]


def _load_report_runs(dirs: Sequence[Path]) -> list[Run]:
    runs: list[Run] = []
    for path in _report_files(dirs):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("eval_report_unreadable", file=path.name)
            continue
        if not isinstance(report, Mapping):
            continue
        mtime = datetime.fromtimestamp(path.stat().st_mtime, UTC).replace(tzinfo=None)
        run = run_from_report(path.stem, report, mtime)
        if run is not None:
            runs.append(run)
    return sorted(runs, key=lambda r: (r.started_at, r.run_id))


async def report_runs(dirs: Sequence[Path]) -> list[Run]:
    """Benchmark runs, oldest first (file IO off the event loop)."""
    return await asyncio.to_thread(_load_report_runs, dirs)


def _share(cond: Any) -> Any:
    return func.avg(case((cond, 1.0), else_=0.0))


async def live_runs(db: AsyncSession, limit: int = MAX_RUNS) -> list[Run]:
    """Online evaluation per UTC day (one statement), oldest first."""
    rec = EvaluationRecord
    day = func.date_trunc(literal_column("'day'"), rec.created_at)
    grounded = case((rec.grounded.is_(True), 1.0), (rec.grounded.is_(False), 0.0))
    stmt = (
        select(
            day,
            func.min(rec.created_at),
            func.count(rec.id),
            _share(rec.passed.is_(True)),
            func.avg(rec.retrieval_recall),
            func.avg(cast(grounded, Float)),
            func.avg(rec.faithfulness),
            func.avg(rec.citation_precision),
            _share(rec.safety_passed.is_(True)),
        )
        .group_by(day)
        .order_by(day.desc())
        .limit(limit)
    )
    runs = []
    for row in (await db.execute(stmt)).all():
        faithfulness = _number(row[6])
        pass_rate = float(row[3] or 0.0)
        runs.append(
            Run(
                run_id=f"{LIVE_PREFIX}{row[0].date().isoformat()}",
                started_at=row[1],
                n_cases=int(row[2]),
                pass_rate=pass_rate,
                scores={
                    "retrieval_recall": _number(row[4]),
                    "groundedness": _number(row[5]),
                    "faithfulness": faithfulness / JUDGE_SCALE
                    if faithfulness is not None
                    else None,
                    "citation_precision": _number(row[7]),
                    "safety": _number(row[8]),
                    "pass_rate": pass_rate,
                },
            )
        )
    return list(reversed(runs))


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


def insight_fields(runs: Sequence[Run]) -> dict[str, Any]:
    """``quality_dimensions`` (last two runs) and ``pass_rate_trend`` (oldest first)."""
    trend = list(runs)[-MAX_RUNS:]
    current = trend[-1] if trend else None
    previous = trend[-2] if len(trend) > 1 else None
    dimensions = (
        [
            {
                "key": key,
                "label": label,
                "current": _rounded(current.scores.get(key)),
                "previous": _rounded(previous.scores.get(key)) if previous else None,
            }
            for key, label in DIMENSIONS
        ]
        if current
        else []
    )
    return {
        "quality_dimensions": {
            "current_run": current.ref() if current else None,
            "previous_run": previous.ref() if previous else None,
            "dimensions": dimensions,
        },
        "pass_rate_trend": [
            {**run.ref(), "pass_rate": round(run.pass_rate, 6)} for run in trend
        ],
    }
