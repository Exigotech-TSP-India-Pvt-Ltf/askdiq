"""Small shared statistics helpers (percentiles) used by /metrics/latency
and the evaluation harness, so both compute percentiles the same way.
"""


def percentile(values: list[float], pct: float) -> float:
    """Return the `pct` percentile (0-1) of `values` using nearest-rank."""
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(
        int(len(ordered) * pct) if pct < 1 else len(ordered) - 1, len(ordered) - 1
    )
    return ordered[idx]
