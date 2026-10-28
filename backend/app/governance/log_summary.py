"""Human-readable summaries and service categories for audit-log rows (G11).

``summarize(action, detail)`` turns a raw ``audit_log.detail`` into a one-line
message via a per-action registry; ``service_for(action)`` maps the action to
a stable category (auth, query, ingest, feedback, admin, system) instead of
echoing the action itself.
"""

from collections.abc import Callable, Mapping
from typing import Any, Literal

Service = Literal["auth", "query", "ingest", "feedback", "admin", "system"]
Summarizer = Callable[[Mapping[str, Any]], str]

_MAX_SUMMARY = 200

SERVICE_BY_ACTION: dict[str, Service] = {
    "query": "query",
    "llm_call_failed": "query",
    "llm_retry": "query",
    "online_eval": "query",
    "session_created": "query",
    "session_rotated": "query",
    "session_expired": "query",
    "session_resumed": "query",
    "ingest": "ingest",
    "document_delete": "ingest",
    "user_feedback": "feedback",
    "negative_feedback_spike": "feedback",
    "feedback_promoted_to_eval_case": "feedback",
    "test_ip_added": "admin",
    "test_ip_updated": "admin",
    "test_ip_removed": "admin",
    "usage_export": "admin",
    "governance_logs_export": "admin",
    "alert_acknowledged": "admin",
    "alert_resolved": "admin",
    "rate_limited": "system",
    "server_error": "system",
}

AUTH_PREFIXES = ("auth", "login", "logout", "token", "register")
SERVICES: tuple[Service, ...] = (
    "auth",
    "query",
    "ingest",
    "feedback",
    "admin",
    "system",
)

# Severities, most to least severe. CRITICAL and ALERT rows are "incidents".
LEVELS: tuple[str, ...] = ("CRITICAL", "ALERT", "ERROR", "WARNING", "INFO")
INCIDENT_LEVELS: tuple[str, ...] = ("CRITICAL", "ALERT")

# Level for actions whose severity does not come from guardrail flags.
LEVEL_BY_ACTION: dict[str, str] = {
    "server_error": "ERROR",
    "llm_call_failed": "ERROR",
    "llm_retry": "WARNING",
    "rate_limited": "WARNING",
    "negative_feedback_spike": "ALERT",
}

# Guardrail flag -> severity, checked from most to least severe. Anything not
# listed (e.g. a normal, unflagged, grounded query) falls through to
# ``LEVEL_BY_ACTION`` and then INFO.
LEVEL_FLAGS: tuple[tuple[str, frozenset[str]], ...] = (
    (
        "CRITICAL",
        frozenset(
            {"llm_call_failed", "groundedness_check_failed", "secret_leak_detected"}
        ),
    ),
    (
        "ALERT",
        frozenset(
            {
                "unsafe_input_detected",
                "spam_detected",
                "pii_leak_detected",
                "online_unfaithful",
            }
        ),
    ),
    (
        "ERROR",
        frozenset(
            {
                "empty_llm_output",
                "empty_answer",
                "no_retrieved_sources",
                "unsupported_numeric_claim",
                "unsupported_claim",
            }
        ),
    ),
    (
        "WARNING",
        frozenset(
            {
                "low_retrieval_confidence",
                "possible_hallucination",
                "query_too_long",
                "empty_query",
            }
        ),
    ),
)


def level_for(detail: Mapping[str, Any] | None, action: str | None = None) -> str:
    """Severity of an audit row (Critical/Alert/Error/Warning/Info).

    Flag-carrying events (query, online_eval) use their guardrail flags; an
    explicit ``grounded: false`` is a WARNING; other actions use
    ``LEVEL_BY_ACTION``. ``app.governance.logs.level_sql`` mirrors this in SQL.
    """
    data = detail if isinstance(detail, Mapping) else {}
    flags = set(data.get("guardrail_flags") or [])
    for level, level_flags in LEVEL_FLAGS:
        if flags & level_flags:
            return level
    if data.get("grounded") is False:
        return "WARNING"
    if action in LEVEL_BY_ACTION:
        return LEVEL_BY_ACTION[action]
    return "INFO"


def service_for(action: str) -> Service:
    """Category for an audit action; unknown actions fall back safely."""
    if action in SERVICE_BY_ACTION:
        return SERVICE_BY_ACTION[action]
    if action.startswith(AUTH_PREFIXES):
        return "auth"
    return "system"


def _clip(text: str) -> str:
    return text if len(text) <= _MAX_SUMMARY else text[: _MAX_SUMMARY - 1] + "…"


def _flags(d: Mapping[str, Any]) -> str:
    flags = d.get("guardrail_flags") or []
    return f" [{', '.join(map(str, flags))}]" if flags else ""


def _query(d: Mapping[str, Any]) -> str:
    grounded = d.get("grounded")
    state = d.get("outcome") or (
        "grounded" if grounded else "ungrounded" if grounded is False else None
    )
    sources = d.get("source_ids")
    parts = [f'Query "{d.get("query", "")}"']
    if state:
        parts.append(str(state))
    if isinstance(sources, list):
        parts.append(f"{len(sources)} source{'' if len(sources) == 1 else 's'}")
    return " · ".join(parts) + _flags(d)


def _ingest(d: Mapping[str, Any]) -> str:
    return (
        f"Ingested {d.get('source_name', 'document')} ({d.get('status', 'done')}): "
        f"+{d.get('chunks_added', 0)} / ={d.get('chunks_kept', 0)} "
        f"/ -{d.get('chunks_removed', 0)} chunks"
    )


def _document_delete(d: Mapping[str, Any]) -> str:
    return (
        f"Deleted document {d.get('source_name', d.get('document_id', ''))} "
        f"({d.get('chunks_removed', 0)} chunks)"
    )


def _user_feedback(d: Mapping[str, Any]) -> str:
    comment = d.get("comment")
    return f"Feedback {d.get('rating', '?')}/5" + (f': "{comment}"' if comment else "")


def _negative_spike(d: Mapping[str, Any]) -> str:
    rate = d.get("negative_rate")
    pct = f"{rate * 100:.0f}%" if isinstance(rate, (int, float)) else "?"
    return f"Negative feedback spike: {pct} of {d.get('total_1h', '?')} ratings in 1h"


def _promoted(d: Mapping[str, Any]) -> str:
    return f"Feedback promoted to eval case {d.get('case_id', '')}".rstrip()


def _online_eval(d: Mapping[str, Any]) -> str:
    return "Online evaluation flagged answer" + _flags(d)


def _test_ip(verb: str) -> Summarizer:
    return lambda d: f"Test IP {d.get('ip_address', '')} {verb}"


def _usage_export(d: Mapping[str, Any]) -> str:
    return f"Usage export ({d.get('tab', 'all')}, audience {d.get('audience', 'all')})"


def _logs_export(d: Mapping[str, Any]) -> str:
    filters = d.get("filters")
    parts = (
        [f"{k}={v}" for k, v in sorted(filters.items()) if v not in (None, "", [])]
        if isinstance(filters, Mapping)
        else []
    )
    return f"Governance logs export ({', '.join(parts) or 'all'})"


def _rate_limited(d: Mapping[str, Any]) -> str:
    return (
        f"Rate limited ({d.get('scope', 'unknown')}): limit {d.get('limit', '?')}/min, "
        f"retry in {d.get('retry_after_s', '?')}s"
    )


def _server_error(d: Mapping[str, Any]) -> str:
    kind = f" {d['error_type']}" if d.get("error_type") else ""
    return f"HTTP {d.get('status_code', 500)}{kind} on {d.get('path', '?')}"


def _llm_call_failed(d: Mapping[str, Any]) -> str:
    retry = " (retrying)" if d.get("will_retry") else ""
    return (
        f"LLM call failed: {d.get('error_type', 'error')} "
        f"(attempt {d.get('attempt', '?')}/{d.get('max_attempts', '?')}){retry}"
    )


def _llm_retry(d: Mapping[str, Any]) -> str:
    return (
        f"LLM retry {d.get('attempt', '?')}/{d.get('max_attempts', '?')} "
        f"after {d.get('reason', 'failure')}"
    )


def _session(verb: str) -> Summarizer:
    return lambda d: f"Session {d.get('session_id') or '?'} {verb}"


def _session_resumed(d: Mapping[str, Any]) -> str:
    idle = d.get("idle_minutes")
    suffix = f" after {idle} min idle" if isinstance(idle, int) else ""
    return f"Session {d.get('session_id') or '?'} resumed{suffix}"


def _session_rotated(d: Mapping[str, Any]) -> str:
    return (
        f"Session rotated ({d.get('reason', 'unknown')}): "
        f"{d.get('from_session_id', '?')} -> {d.get('to_session_id', '?')}"
    )


SUMMARIZERS: dict[str, Summarizer] = {
    "query": _query,
    "ingest": _ingest,
    "document_delete": _document_delete,
    "user_feedback": _user_feedback,
    "negative_feedback_spike": _negative_spike,
    "feedback_promoted_to_eval_case": _promoted,
    "online_eval": _online_eval,
    "test_ip_added": _test_ip("added"),
    "test_ip_updated": _test_ip("updated"),
    "test_ip_removed": _test_ip("removed"),
    "usage_export": _usage_export,
    "governance_logs_export": _logs_export,
    "rate_limited": _rate_limited,
    "server_error": _server_error,
    "llm_call_failed": _llm_call_failed,
    "llm_retry": _llm_retry,
    "session_created": _session("created"),
    "session_expired": _session("expired"),
    "session_resumed": _session_resumed,
    "session_rotated": _session_rotated,
}


def _fallback(action: str, detail: Mapping[str, Any]) -> str:
    label = action.replace("_", " ").strip().capitalize() or "Event"
    keys = ", ".join(sorted(str(k) for k in detail)[:5])
    return f"{label} ({keys})" if keys else label


def summarize(action: str, detail: Mapping[str, Any] | None) -> str:
    """One-line message for an audit row; never raises."""
    data: Mapping[str, Any] = detail if isinstance(detail, Mapping) else {}
    summarizer = SUMMARIZERS.get(action)
    try:
        text = summarizer(data) if summarizer else _fallback(action, data)
    except Exception:
        text = _fallback(action, data)
    return _clip(text)
