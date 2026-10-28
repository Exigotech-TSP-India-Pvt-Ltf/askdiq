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
LIVE_CLIENT_CONCURRENCY (default 4; shared by all threads in the process).
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
from functools import cache
from pathlib import Path
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
# Last-resort location of the backend that owns the local ``.env``.
DEFAULT_ENV_BACKEND_DIR = r"C:\Projects\askdiq\backend"
MAX_ATTEMPTS = 3
BACKOFF_BASE_S = 1.0
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

_CONCURRENCY = max(1, int(os.environ.get("LIVE_CLIENT_CONCURRENCY", "4")))
_SEMAPHORE = threading.BoundedSemaphore(_CONCURRENCY)
_PROCESS_CLIENT_ID = f"live-{uuid.uuid4()}"

INTERNAL_PHRASES = (
    "supplied context",
    "knowledge base",
    "the documents",
    "materials",
    "provided information",
    "as an ai",
)
ASSURANCE_WORDS = ("guaranteed", "100% safe", "certified")
FALLBACK_OUTCOMES = frozenset({"ungrounded", "llm_failed", "empty_llm_output"})
NO_SOURCE_FLAG_MARKERS = ("spam", "unsafe", "off_topic", "refus")
NO_SOURCE_OUTCOMES = frozenset({"blocked", "refused", "off_topic", "spam", "unsafe"})
CLARIFY_OUTCOME = "clarify"


class LiveClientError(RuntimeError):
    """Request failed after retries (message never contains headers or keys)."""


@cache
def _service_key() -> str:
    # Imported lazily so tests and library users only need it for real calls.
    backend_dir = Path(__file__).resolve().parents[1]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    from app.core.config import get_settings  # noqa: PLC0415

    # Settings load ``.env`` relative to the cwd. A git worktree has none, so
    # run get_settings() from the first backend dir that has one (existence
    # check only; the file is never read here).
    candidates = [
        os.environ.get("LIVE_CLIENT_BACKEND_DIR"),
        str(Path.cwd()),
        str(backend_dir),
        DEFAULT_ENV_BACKEND_DIR,
    ]
    env_dir = next((d for d in candidates if d and (Path(d) / ".env").is_file()), None)
    if env_dir is None:
        return get_settings().api_key_name
    previous = Path.cwd()
    os.chdir(env_dir)
    try:
        return get_settings().api_key_name
    finally:
        os.chdir(previous)


def _base_url() -> str:
    return os.environ.get("LIVE_CLIENT_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def _post_with_retry(
    client: httpx.Client, payload: dict, headers: dict
) -> httpx.Response:
    last = ""
    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = client.post("/query", json=payload, headers=headers)
        except httpx.TransportError as exc:
            last = type(exc).__name__
        else:
            if resp.status_code not in RETRY_STATUSES:
                return resp
            last = f"HTTP {resp.status_code}"
            if attempt == MAX_ATTEMPTS - 1:
                return resp
        time.sleep(BACKOFF_BASE_S * 2**attempt)
    raise LiveClientError(f"/query failed after {MAX_ATTEMPTS} attempts ({last})")


def ask(
    query: str,
    session_id: str | None = None,
    new_session: bool = False,
    clarification_answers: list[dict] | None = None,
    timeout: float = 120,
    client_id: str | None = None,
    transport: httpx.BaseTransport | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """POST /query and return the parsed JSON plus ``wall_ms`` and ``client_id``.

    Reuse the returned ``client_id`` with ``session_id`` to continue a session
    (sessions belong to the client id). Raises LiveClientError on non-2xx.
    """
    cid = client_id or _PROCESS_CLIENT_ID
    payload: dict[str, Any] = {"query": query}
    if session_id:
        payload["session_id"] = session_id
    if new_session:
        payload["new_session"] = True
    if clarification_answers:
        payload["clarification_answers"] = clarification_answers
    headers = {
        "X-API-Key": api_key if api_key is not None else _service_key(),
        "X-Client-Id": cid,
    }
    with _SEMAPHORE:
        started = time.perf_counter()
        with httpx.Client(
            base_url=_base_url(), timeout=timeout, transport=transport
        ) as client:
            resp = _post_with_retry(client, payload, headers)
        wall_ms = round((time.perf_counter() - started) * 1000, 1)
    if resp.status_code >= 400:
        raise LiveClientError(
            f"/query returned HTTP {resp.status_code}: {resp.text[:300]}"
        )
    data = resp.json()
    data["wall_ms"] = wall_ms
    data["client_id"] = cid
    return data


class ConversationRunner:
    """Multi-turn conversation: one client id, session_id kept across turns."""

    def __init__(self, **ask_kwargs: Any) -> None:
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


def check_answer(resp: dict, rules: dict | None = None) -> list[str]:
    """Return rule violations (empty list = clean).

    rules (all optional): ``kind`` to force "answer"/"fallback"/"refusal"/"clarify"
    instead of auto-detecting, ``allow_assurance`` (bool), ``allow_internal``
    (bool), ``min_options`` (int, default 2).
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
            f"assurance wording: '{w}'" for w in ASSURANCE_WORDS if _has(answer, w)
        ]
    if kind == "fallback" and "specialist" not in answer.lower():
        problems.append("fallback reply does not mention 'specialist'")
    if kind == "refusal" and resp.get("sources"):
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
