"""Extract the Chatbot Insights reference dataset into a JSON test fixture.

Reads ``window.__EMBED__={conversations:[...],exchanges:[...]}`` from the
standalone insights HTML (planchanges4 B6.4.1 / newplan section 6.4) and
writes ``backend/tests/fixtures/insights_reference.json``.

The HTML is untrusted input: nothing is evaluated. Each array is decoded with
``json.JSONDecoder.raw_decode`` starting at its ``[`` and must be a list of
flat JSON objects with string/number/bool/null values.

Usage (from ``backend/``)::

    venv\\Scripts\\python.exe -I scripts/extract_insights_fixture.py \\
        ..\\ExigoTech_Chatbot_Insights_standalone.html
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

EMBED_MARKER = "window.__EMBED__="
DATASET_KEYS = ("conversations", "exchanges")
DEFAULT_OUTPUT = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "insights_reference.json"
)
MAX_HTML_BYTES = 20 * 1024 * 1024
_SCALAR_TYPES = (str, int, float, bool, type(None))


class FixtureExtractionError(ValueError):
    """The HTML does not contain a well-formed ``__EMBED__`` dataset."""


def _decode_array(text: str, start: int, key: str) -> list[dict[str, Any]]:
    match = re.compile(rf"\b{key}\s*:\s*\[").search(text, start)
    if match is None:
        raise FixtureExtractionError(f"'{key}' array not found after {EMBED_MARKER}")
    try:
        value, _end = json.JSONDecoder().raw_decode(text, match.end() - 1)
    except json.JSONDecodeError as exc:
        raise FixtureExtractionError(f"'{key}' is not valid JSON: {exc}") from exc
    if not isinstance(value, list):
        raise FixtureExtractionError(f"'{key}' must be a JSON array")
    for row in value:
        if not isinstance(row, dict) or not all(
            isinstance(v, _SCALAR_TYPES) for v in row.values()
        ):
            raise FixtureExtractionError(f"'{key}' rows must be flat JSON objects")
    return value


def extract_embed(html: str) -> dict[str, list[dict[str, Any]]]:
    """Return ``{"conversations": [...], "exchanges": [...]}`` from the HTML."""
    start = html.find(EMBED_MARKER)
    if start < 0:
        raise FixtureExtractionError(f"{EMBED_MARKER} not found")
    return {key: _decode_array(html, start, key) for key in DATASET_KEYS}


def build_fixture(html: str, source_name: str) -> dict[str, Any]:
    data = extract_embed(html)
    return {
        "source": source_name,
        "conversation_count": len(data["conversations"]),
        "exchange_count": len(data["exchanges"]),
        **data,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("html", type=Path, help="Path to the standalone insights HTML")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    if args.html.stat().st_size > MAX_HTML_BYTES:
        print(f"Refusing to read {args.html}: larger than {MAX_HTML_BYTES} bytes")
        return 2
    try:
        fixture = build_fixture(
            args.html.read_text(encoding="utf-8"), source_name=args.html.name
        )
    except FixtureExtractionError as exc:
        print(f"Extraction failed: {exc}")
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(fixture, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        f"Wrote {args.out}: {fixture['conversation_count']} conversations, "
        f"{fixture['exchange_count']} exchanges"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
