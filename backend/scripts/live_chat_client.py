"""Shared client for live-testing POST /query against a locally running backend.

Usage (run from the ``backend`` directory)::

    venv/Scripts/python.exe scripts/live_chat_client.py "What is DeployIQ?" [--json]

As a library::

    sys.path.insert(0, "<repo>/backend/scripts"); import live_chat_client as lc
    resp = lc.ask("What is DeployIQ?")              # dict + "wall_ms"
    run = lc.ConversationRunner(); run.ask("q1"); run.ask("follow-up")
    problems = lc.check_answer(resp, {"kind": "fallback"})   # [] when clean

Auth mirrors the website path (app/auth/deps.py ``require_chat_actor``): the
service key (``API_KEY_NAME``) goes in ``X-API-Key`` and a per-visitor
``X-Client-Id`` owns the chat sessions. The key is read in-process via
``get_settings()`` and is never printed or logged.

Env: LIVE_CLIENT_BACKEND_DIR (dir holding ``.env``; default: cwd, then
C:\\Projects\\askdiq\\backend), LIVE_CLIENT_BASE_URL (default http://127.0.0.1:8000),
LIVE_CLIENT_CONCURRENCY (default 4; shared by all threads in the process),
LIVE_CLIENT_RUNNER_CALLS_PER_MIN (default 8; per ConversationRunner, 0 = no pacing).

Rate limits: the server allows 30 q/min per IP and 10/min per client id, so each
ConversationRunner has its own client id and paces itself below 10/min. HTTP 429
is retried after 15s (up to 4 tries) and counted in the response's ``rate_limited``.
One ``httpx.Client`` is shared per process: building one loads the SSL context,
which took 7-21s on a loaded machine and made every call look slow.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
import uuid
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import Any, NamedTuple

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
# Last-resort location of the backend that owns the local ``.env``.
DEFAULT_ENV_BACKEND_DIR = r"C:\Projects\askdiq\backend"
MAX_ATTEMPTS = 3
BACKOFF_BASE_S = 1.0
RETRY_STATUSES = frozenset({500, 502, 503, 504})
RATE_LIMIT_STATUS = 429
RATE_LIMIT_WAIT_S = 15.0
RATE_LIMIT_ATTEMPTS = 4
DEFAULT_RUNNER_CALLS_PER_MIN = float(
    os.environ.get("LIVE_CLIENT_RUNNER_CALLS_PER_MIN", "8")
)

_CONCURRENCY = max(1, int(os.environ.get("LIVE_CLIENT_CONCURRENCY", "4")))
_SEMAPHORE = threading.BoundedSemaphore(_CONCURRENCY)
_PROCESS_CLIENT_ID = f"live-{uuid.uuid4()}"
_CLIENT: httpx.Client | None = None
_CLIENT_LOCK = threading.Lock()

INTERNAL_PHRASES = (
    "supplied context",
    "knowledge base",
    "the documents",
    "materials",
    "provided information",
    "as an ai",
)
ASSURANCE_WORDS = ("guaranteed", "100% safe", "certified")
_NEGATION = re.compile(
    r"\b(?:not|no|never|cannot|without|unable)\b|n['\u2019]t\b", re.IGNORECASE
)
_CLAUSE_SPLIT = re.compile(r"[.!?;\n]+|\b(?:but|however)\b", re.IGNORECASE)
FALLBACK_OUTCOMES = frozenset({"ungrounded", "llm_failed", "empty_llm_output"})
NO_SOURCE_FLAG_MARKERS = ("spam", "unsafe", "off_topic", "refus")
NO_SOURCE_OUTCOMES = frozenset({"blocked", "refused", "off_topic", "spam", "unsafe"})
CLARIFY_OUTCOME = "clarify"


class LiveClientError(RuntimeError):
    """Request failed after retries (message never contains headers or keys)."""


def _in_env_dir(fn: Callable[[], Any]) -> Any:
    """Run ``fn`` with the backend on ``sys.path`` and cwd at a dir with ``.env``.

    Settings load ``.env`` relative to the cwd. A git worktree has none, so
    use the first backend dir that has one (existence check only; the file is
    never read here).
    """
    backend_dir = Path(__file__).resolve().parents[1]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    candidates = [
        os.environ.get("LIVE_CLIENT_BACKEND_DIR"),
        str(Path.cwd()),
        str(backend_dir),
        DEFAULT_ENV_BACKEND_DIR,
    ]
    env_dir = next((d for d in candidates if d and (Path(d) / ".env").is_file()), None)
    if env_dir is None:
        return fn()
    previous = Path.cwd()
    os.chdir(env_dir)
    try:
        return fn()
    finally:
        os.chdir(previous)


@cache
def _service_key() -> str:
    # Imported lazily so tests and library users only need it for real calls.
    def load() -> str:
        from app.core.config import get_settings  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

        return get_settings().api_key_name

    return _in_env_dir(load)


class FixedLines(NamedTuple):
    """Fixed customer-facing lines from ``app.harness.prompt_catalog``."""

    no_answer: str
    try_again: str
    off_topic: str | None
    sales: re.Pattern[str]
    partial: re.Pattern[str]
    fallback_line: Callable[[list[str]], str]


_SLOT = "\x00"


def _slot_pattern(text_with_slots: str) -> re.Pattern[str]:
    """Regex for a fixed line whose ``_SLOT`` markers are free text."""
    pieces = text_with_slots.split(_SLOT)
    body = r".+?".join(re.escape(p) for p in pieces)
    return re.compile(rf"{body}(?=\s|$)", re.IGNORECASE)


@cache
def fixed_lines() -> FixedLines:
    """Load the fallback contract from the app (cached; needs settings)."""

    def load() -> FixedLines:
        from app.harness import prompt_catalog as cat  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

        try:
            off_topic: str | None = cat.off_topic_line()
        except Exception:  # noqa: BLE001 - settings unavailable  # pylint: disable=broad-exception-caught
            off_topic = None
        return FixedLines(
            no_answer=cat.NO_ANSWER_LINE,
            try_again=cat.TRY_AGAIN_LINE,
            off_topic=off_topic,
            sales=_slot_pattern(
                cat.SALES_CONTACT_LINE.format(phone=_SLOT, email=_SLOT)
            ),
            partial=_slot_pattern(cat.partial_line([_SLOT])),
            fallback_line=cat.fallback_line,
        )

    return _in_env_dir(load)


def _base_url() -> str:
    return os.environ.get("LIVE_CLIENT_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def _shared_client() -> httpx.Client:
    """One lazily built, thread-safe client; timeout is set per request."""
    global _CLIENT  # noqa: PLW0603  # pylint: disable=global-statement
    with _CLIENT_LOCK:
        if _CLIENT is None:
            _CLIENT = httpx.Client()
        return _CLIENT


def _post_with_retry(
    client: httpx.Client, url: str, payload: dict, headers: dict, timeout: float
) -> tuple[httpx.Response, int]:
    """POST with retries; returns ``(response, rate_limited_count)``."""
    rate_limited = errors = 0
    while True:
        try:
            resp = client.post(url, json=payload, headers=headers, timeout=timeout)
        except httpx.TransportError as exc:
            errors += 1
            if errors >= MAX_ATTEMPTS:
                raise LiveClientError(
                    f"/query failed after {MAX_ATTEMPTS} attempts "
                    f"({type(exc).__name__})"
                ) from exc
            time.sleep(BACKOFF_BASE_S * 2 ** (errors - 1))
            continue
        if resp.status_code == RATE_LIMIT_STATUS:
            rate_limited += 1
            if rate_limited >= RATE_LIMIT_ATTEMPTS:
                return resp, rate_limited
            time.sleep(RATE_LIMIT_WAIT_S)
        elif resp.status_code in RETRY_STATUSES:
            errors += 1
            if errors >= MAX_ATTEMPTS:
                return resp, rate_limited
            time.sleep(BACKOFF_BASE_S * 2 ** (errors - 1))
        else:
            return resp, rate_limited


def _query_payload(
    query: str,
    session_id: str | None,
    new_session: bool,
    clarification_answers: list[dict] | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"query": query}
    if session_id:
        payload["session_id"] = session_id
    if new_session:
        payload["new_session"] = True
    if clarification_answers:
        payload["clarification_answers"] = clarification_answers
    return payload


def _timed_post(
    url: str,
    payload: dict,
    headers: dict,
    timeout: float,
    transport: httpx.BaseTransport | None,
) -> tuple[httpx.Response, int, float]:
    """``(response, rate_limited_count, wall_ms)`` for one paced POST."""
    with _SEMAPHORE:
        if transport is not None:
            with httpx.Client(transport=transport) as client:
                started = time.perf_counter()
                resp, rate_limited = _post_with_retry(
                    client, url, payload, headers, timeout
                )
        else:
            client = _shared_client()  # built before the timer starts
            started = time.perf_counter()
            resp, rate_limited = _post_with_retry(
                client, url, payload, headers, timeout
            )
        wall_ms = round((time.perf_counter() - started) * 1000, 1)
    return resp, rate_limited, wall_ms


def ask(  # pylint: disable=too-many-arguments,too-many-positional-arguments  # public client API
    query: str,
    session_id: str | None = None,
    new_session: bool = False,
    clarification_answers: list[dict] | None = None,
    timeout: float = 120,
    client_id: str | None = None,
    transport: httpx.BaseTransport | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """POST /query and return the parsed JSON plus ``wall_ms``, ``client_id``
    and ``rate_limited`` (number of HTTP 429 responses retried).

    Reuse the returned ``client_id`` with ``session_id`` to continue a session
    (sessions belong to the client id). Raises LiveClientError on non-2xx.
    """
    cid = client_id or _PROCESS_CLIENT_ID
    payload = _query_payload(query, session_id, new_session, clarification_answers)
    headers = {
        "X-API-Key": api_key if api_key is not None else _service_key(),
        "X-Client-Id": cid,
    }
    resp, rate_limited, wall_ms = _timed_post(
        f"{_base_url()}/query", payload, headers, timeout, transport
    )
    if resp.status_code >= 400:
        raise LiveClientError(
            f"/query returned HTTP {resp.status_code}: {resp.text[:300]}"
        )
    data = resp.json()
    data["wall_ms"] = wall_ms
    data["client_id"] = cid
    data["rate_limited"] = rate_limited
    return data


class ConversationRunner:  # pylint: disable=too-few-public-methods  # one ask() per turn
    """Multi-turn conversation: own client id, session_id kept across turns.

    Calls are paced to ``calls_per_min`` (default 8, under the server's 10/min
    per-client limit); pass 0 or None to disable.
    """

    def __init__(
        self,
        calls_per_min: float | None = DEFAULT_RUNNER_CALLS_PER_MIN,
        **ask_kwargs: Any,
    ) -> None:
        self._min_interval = 60.0 / calls_per_min if calls_per_min else 0.0
        self._next_call_at = 0.0
        self.client_id = f"live-{uuid.uuid4()}"
        self.session_id: str | None = None
        self.turns: list[dict[str, Any]] = []
        self._kwargs = ask_kwargs

    def ask(
        self,
        query: str,
        clarification_answers: list[dict] | None = None,
        new_session: bool = False,
    ) -> dict[str, Any]:
        self._pace()
        resp = ask(
            query,
            session_id=None if new_session else self.session_id,
            new_session=new_session,
            clarification_answers=clarification_answers,
            client_id=self.client_id,
            **self._kwargs,
        )
        self.session_id = resp.get("session_id") or self.session_id
        self.turns.append(resp)
        return resp

    def _pace(self) -> None:
        now = time.monotonic()
        if self._min_interval and now < self._next_call_at:
            time.sleep(self._next_call_at - now)
            now = time.monotonic()
        self._next_call_at = now + self._min_interval


def ask_case(case: Any, runner: ConversationRunner | None = None) -> dict[str, Any]:
    """Run one ``EvalCase`` live: replay its history's user turns, then ask.

    A live run cannot inject ``case.history``, so ``case.replay_turns()`` is sent
    first on the same session. Returns the final response.
    """
    runner = runner or ConversationRunner()
    for turn in case.replay_turns():
        runner.ask(turn)
    return runner.ask(case.query)


def _has(text: str, phrase: str) -> bool:
    pattern = rf"(?<!\w){re.escape(phrase)}(?!\w)"
    return re.search(pattern, text, re.IGNORECASE) is not None


def response_kind(resp: dict) -> str:
    """Classify as clarify / refusal / fallback / answer from outcome and flags."""
    outcome = str(resp.get("outcome") or "")
    flags = [str(f).lower() for f in resp.get("guardrail_flags") or []]
    if outcome == CLARIFY_OUTCOME:
        return "clarify"
    if outcome in NO_SOURCE_OUTCOMES or any(
        m in f for f in flags for m in NO_SOURCE_FLAG_MARKERS
    ):
        return "refusal"
    if outcome in FALLBACK_OUTCOMES or "fallback" in flags:
        return "fallback"
    return "answer"


def check_clarification(resp: dict, min_options: int = 2) -> list[str]:
    clar = resp.get("clarification")
    if not clar:
        return ["clarify outcome without clarification payload"]
    problems: list[str] = []
    questions = clar.get("questions") or []
    if not questions:
        problems.append("clarification has no questions")
    for q in questions:
        n = len(q.get("options") or [])
        if n < min_options:
            problems.append(f"clarification question {q.get('id')} has {n} options")
        if not (q.get("question") or "").strip():
            problems.append(f"clarification question {q.get('id')} is empty")
    return problems


def _asserts(answer: str, word: str) -> bool:
    """True when ``word`` appears in a clause that carries no negation."""
    return any(
        _has(clause, word) and not _NEGATION.search(clause)
        for clause in _CLAUSE_SPLIT.split(answer)
        if clause
    )


def _norm(text: str) -> str:
    return " ".join(text.split())


@cache
def _fallback_for(flags: tuple[str, ...]) -> str | None:
    lines = fixed_lines()
    try:
        return _in_env_dir(lambda: lines.fallback_line(list(flags)))
    except Exception:  # noqa: BLE001 - settings unavailable  # pylint: disable=broad-exception-caught
        return None


def _residue(
    answer: str, exact: set[str | None], patterns: tuple[re.Pattern[str], ...]
) -> str:
    """What is left of ``answer`` after removing the fixed lines."""
    text = _norm(answer)
    for line in sorted((x for x in exact if x), key=len, reverse=True):
        text = text.replace(_norm(line), " ")
    for pattern in patterns:
        text = pattern.sub(" ", text)
    return text.strip(" .")


_PERSONA_PREFIX = "persona_"


@cache
def _template_texts(
    template_id: str | None,
) -> tuple[frozenset[str], tuple[re.Pattern[str], ...]]:
    """Approved POLICY / PERSONA text(s) of a logged ``refusal_template`` id.

    Returns exact texts plus slot patterns (the privacy reply's link). Unknown
    ids, the ``partial`` line and unavailable settings give nothing.
    """
    if not template_id or template_id == "partial":
        return frozenset(), ()

    def load() -> tuple[frozenset[str], tuple[re.Pattern[str], ...]]:
        from app.harness import prompt_catalog as cat  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

        if template_id.startswith(_PERSONA_PREFIX):
            intent = template_id.removeprefix(_PERSONA_PREFIX)
            if intent not in cat.PERSONA_TEMPLATES:
                return frozenset(), ()
            linked = _slot_pattern(cat.persona_template(intent, privacy_url=_SLOT))
            return frozenset({cat.persona_template(intent)}), (linked,)
        if template_id in cat.POLICY_TEMPLATES:
            return frozenset({cat.policy_template(template_id)}), ()
        return frozenset(), ()

    try:
        return _in_env_dir(load)
    except Exception:  # noqa: BLE001 - settings unavailable  # pylint: disable=broad-exception-caught
        return frozenset(), ()


@cache
def _flag_template_id(flags: tuple[str, ...], outcome: str) -> str | None:
    """The app's template id for ``flags`` when the response logs none."""

    def load() -> str:
        from app.harness import prompt_catalog as cat  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

        return cat.refusal_template_id(list(flags), outcome)

    try:
        return _in_env_dir(load)
    except Exception:  # noqa: BLE001 - settings unavailable  # pylint: disable=broad-exception-caught
        return None


def _fallback_text_problems(
    answer: str, flags: list[str], kind: str, template_id: str | None = None
) -> list[str]:
    """A decline must be made only of the fixed catalog lines for its flags
    (or the approved POLICY / PERSONA template logged as ``refusal_template``)."""
    lines = fixed_lines()
    expected = {_fallback_for(tuple(flags))}
    if kind == "fallback":
        expected |= {lines.no_answer, lines.try_again}
    elif "off_topic" in flags:
        expected.add(lines.off_topic)
    templates, template_patterns = _template_texts(template_id)
    expected |= templates
    if _residue(answer, expected, (lines.sales, *template_patterns)):
        return [f"{kind} reply is not a fixed catalog line: {_norm(answer)[:80]}"]
    return []


def _label_problems(resp: dict, kind: str) -> list[str]:
    """Owner label contract: declines ungrounded/0/no refs, answers grounded+cited."""
    grounded = resp.get("grounded")
    sources = resp.get("sources") or []
    citations = resp.get("citations") or []
    problems: list[str] = []
    if kind in {"fallback", "refusal"}:
        if grounded is not False:
            problems.append(f"{kind} grounded={grounded}, want False")
        if float(resp.get("confidence") or 0) != 0:
            problems.append(f"{kind} confidence={resp.get('confidence')}, want 0")
        if sources:
            problems.append(f"{kind} has {len(sources)} sources")
        if citations:
            problems.append(f"{kind} has {len(citations)} citations")
    elif kind == "answer" and resp.get("outcome") == "answered":
        if grounded is not True:
            problems.append(f"answered grounded={grounded}, want True")
        if not citations:
            problems.append("answered reply has no citations")
    elif kind == "clarify" and grounded is True:
        problems.append("clarify grounded=True")
    return problems


def _answered_is_only_fallback(answer: str) -> bool:
    lines = fixed_lines()
    exact = {lines.no_answer, lines.try_again, lines.off_topic}
    return not _residue(answer, exact, (lines.partial, lines.sales))


def check_answer(
    resp: dict, rules: dict | None = None, query: str | None = None
) -> list[str]:
    """Return rule violations (empty list = clean).

    rules (all optional): ``kind`` to force "answer"/"fallback"/"refusal"/"clarify"
    instead of auto-detecting, ``allow_assurance`` (bool), ``allow_internal``
    (bool), ``min_options`` (int, default 2), ``check_labels`` (bool, default
    True: grounded / confidence / sources / citations contract).

    Fallback and refusal text must be the fixed lines from
    ``app.harness.prompt_catalog`` (``fallback_line(flags)``, ``NO_ANSWER_LINE``,
    ``TRY_AGAIN_LINE``, ``off_topic_line()``, optionally with the
    ``SALES_CONTACT_LINE``) or the POLICY / PERSONA template (regulated_advice,
    identity, persona_*, off_topic, unsafe, ...) named by the response's
    ``token_details.refusal_template``; an "answered" reply must not be only
    those lines.

    Assurance words only count when asserted: not in a negated clause ("we don't
    have details showing ... certified") and not echoed from the visitor's
    ``query``.
    """
    rules = rules or {}
    kind = rules.get("kind") or response_kind(resp)
    answer = resp.get("answer") or ""
    problems: list[str] = []
    if not answer.strip():
        problems.append("empty answer")
    if not rules.get("allow_internal"):
        problems += [
            f"internal wording: '{p}'" for p in INTERNAL_PHRASES if _has(answer, p)
        ]
    if not rules.get("allow_assurance"):
        problems += [
            f"assurance wording: '{w}'"
            for w in ASSURANCE_WORDS
            if _asserts(answer, w) and not (query and _has(query, w))
        ]
    flags = [str(f) for f in resp.get("guardrail_flags") or []]
    if kind in {"fallback", "refusal"} and answer.strip():
        template_id = (resp.get("token_details") or {}).get(
            "refusal_template"
        ) or _flag_template_id(tuple(flags), str(resp.get("outcome") or ""))
        problems += _fallback_text_problems(answer, flags, kind, template_id)
    if kind == "answer" and answer.strip() and _answered_is_only_fallback(answer):
        problems.append(f"answer is only fallback text: {_norm(answer)[:80]}")
    if rules.get("check_labels", True):
        problems += _label_problems(resp, kind)
    elif kind == "refusal" and resp.get("sources"):
        problems.append(f"refusal has {len(resp['sources'])} sources")
    if kind == "clarify":
        problems += check_clarification(resp, int(rules.get("min_options", 2)))
    return problems


def _print_summary(resp: dict) -> None:
    out = sys.stdout.write
    out(f"answer: {resp.get('answer', '')}\n")
    out(f"outcome: {resp.get('outcome')}\n")
    out(f"flags: {resp.get('guardrail_flags') or []}\n")
    out(f"sources: {len(resp.get('sources') or [])}\n")
    out(
        f"labels: grounded={resp.get('grounded')} "
        f"confidence={resp.get('confidence')} "
        f"citations={len(resp.get('citations') or [])}\n"
    )
    questions = (resp.get("clarification") or {}).get("questions") or []
    if questions:
        out(f"clarification headers: {[q.get('header') for q in questions]}\n")
    out(f"latency_ms: {resp.get('wall_ms')}\n")
    out(f"violations: {check_answer(resp) or 'none'}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live /query client")
    parser.add_argument("question")
    parser.add_argument("--json", action="store_true", help="print full response JSON")
    parser.add_argument("--session-id")
    parser.add_argument("--new-session", action="store_true")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        resp = ask(
            args.question, session_id=args.session_id, new_session=args.new_session
        )
    except LiveClientError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 1
    if args.json:
        sys.stdout.write(json.dumps(resp, indent=2, ensure_ascii=False) + "\n")
    else:
        _print_summary(resp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
