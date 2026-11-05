"""Answer-shape repairs applied to the claims that ship, before rendering.

Subtractive and structural only: every shipped word is a verified claim's
or the verbatim body of the page card a claim names.

- Page numbering copied from card headings ("04 \N{EM DASH} Admin Center",
  "Access Control & Identity, 02, Cryptography") is removed.
- A heading dash read as a comma ("Zero Trust, Starter includes") is
  joined back ("Zero Trust Starter includes").
- Image annotations ("Left ring (red)", "(top)", "_(highlighted)_") are cut.
- "Step 1 — Onboard" rows become plain step items ("Onboard").
- A bare card heading as a sentence is dropped when other claims answer, or
  shown with its card's body ("Admin Center, Intune & Purview: Tenant-wide
  governance and data controls.") when it is the only claim.
- A lead cut before its complement ("Deploy IQ uses information to") ends in
  ":" before a list and is dropped otherwise.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.harness.claims import Claim
    from app.schemas.query import SourceChunk

_BREADCRUMB_SEP = chr(0x203A)
_DASHES = f"{chr(0x2014)}{chr(0x2013)}-"
# "04 — Admin Center", "4 Admin Center", "05, Network": a card number + label.
_LEAD_NUMBER_RE = re.compile(
    r"^\s{0,3}(\d{1,2})\s{0,3}[\N{EM DASH}\N{EN DASH}:,.-]?\s{1,3}(?=[A-Z])"
)
# The same numbering inside a claim (never "365", "27001", "v7.0", "24/7").
_INLINE_NUMBER_RE = re.compile(
    r"(?:(?<=,)|(?<=\s))\s{0,2}(\d{1,2})(?:\s{0,2}[\N{EM DASH}\N{EN DASH}:,-])?\s{1,3}(?=[A-Z])"
)
_ITEM_JOIN_RE = re.compile(r"^(?:and|or)\s{1,3}", re.IGNORECASE)
_PADDED_RE = re.compile(r"^0\d$")
_STEP_RE = re.compile(
    r"^\s{0,3}(?:step|stage|phase)\s{1,3}\d{1,2}\s{0,2}[,:.\N{EM DASH}\N{EN DASH}-]?\s{1,3}(?:is\s{1,3})?(?-i:(?=[A-Z0-9]))(\S.*?)\s{0,2}[.]?$",
    re.IGNORECASE,
)
_ANNOTATION_RE = re.compile(
    r"\s{0,2}_?\((?:red|green|blue|orange|yellow|purple|grey|gray|black|white|top|"
    r"bottom|left|right|centre|center|middle|highlighted)\)_?",
    re.IGNORECASE,
)
_DANGLING_RE = re.compile(
    r"\b(?:to|for|of|with|by|from|as|and|or|the|a|an|including|like|into|on|in|"
    r"such\s{1,2}as)\s{0,2}[:.]?$",
    re.IGNORECASE,
)
# A relative clause is never a list's lead; a qualifier is never a bullet.
_BAD_LEAD_RE = re.compile(r"\b(?:that|which|who)\s{0,2}:$", re.IGNORECASE)
_BAD_ITEM_RE = re.compile(
    r"^(?:depending\s{1,2}on|such\s{1,2}as|including|e\.g\.)\b|\bsuch\s{1,2}as\b"
    r"|^(?:includes?|covers?|offers?|provides?)\b",
    re.IGNORECASE,
)
_NON_WORD_RE = re.compile(r"[^\w&]+")
_FINITE_RE = re.compile(
    r"\b(?:is|are|was|were|has|have|had|do|does|can|will|may|must|includes?|"
    r"covers?|offers?|provides?|gives?|helps?|means?|relates?|lists?|uses?|"
    r"supports?|delivers?|ensures?|runs?|works?|adds?|applies|focus(?:es)?)\b",
    re.IGNORECASE,
)
_MAX_HEADING_WORDS = 8
_MIN_STEP_RUN = 2
_MAX_BODY_WORDS = 30
_MAX_STEP_WORDS = 4
_TERMINAL = ".!?:"


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    return " ".join(_NON_WORD_RE.sub(" ", text).split())


def _numbered(text: str) -> tuple[int, str] | None:
    """(number, normalised label) of "04 — Admin Center, Intune & Purview"."""
    text = text.strip().lstrip("#-*• ").strip("*_ ")
    found = _LEAD_NUMBER_RE.match(text)
    if found is None:
        return None
    label = _norm(text[found.end() :])
    return (int(found.group(1)), label) if label else None


class _Page:  # pylint: disable=too-few-public-methods
    """The headings and card bodies the retrieved chunks show."""

    def __init__(self, sources: Sequence[SourceChunk]) -> None:
        self.numbered: set[tuple[int, str]] = set()
        self.headings: dict[str, tuple[int, str]] = {}  # label -> (source, body)
        self.dashed: list[tuple[str, str]] = []
        for index, source in enumerate(sources, start=1):
            meta = source.metadata or {}
            lines = [line.strip() for line in source.content.split("\n")]
            texts = [str(meta.get("section_heading") or "")]
            texts += [str(p) for p in meta.get("section_path") or []]
            head = next((line for line in lines if line), "")
            texts += head.split(_BREADCRUMB_SEP)
            body = " ".join(line for line in lines[1:] if line).strip()
            for text in texts:
                self._heading(text.strip(), index, body)
            for line_no, line in enumerate(lines):
                pair = _numbered(line)
                if pair is not None:
                    self.numbered.add(pair)
                    after = next((x for x in lines[line_no + 1 :] if x), "")
                    if after and _numbered(after) is None and not after.startswith("-"):
                        self.headings.setdefault(pair[1], (index, after))

    def _heading(self, text: str, index: int, body: str) -> None:
        if not text:
            return
        pair = _numbered(text)
        label = pair[1] if pair else _norm(text)
        if pair:
            self.numbered.add(pair)
        if body and label and (label not in self.headings or pair):
            self.headings[label] = (index, body)
        parts = re.split(rf"\s[{_DASHES}]\s", text)
        if len(parts) == 2 and all(parts) and pair is None:  # noqa: PLR2004
            self.dashed.append((parts[0].strip(), parts[1].strip()))

    def is_numbering(self, number: str, rest: str) -> bool:
        if _PADDED_RE.match(number):
            return True
        key, tail = int(number), _norm(rest)
        return any(n == key and tail.startswith(label) for n, label in self.numbered)


def _strip_numbers(text: str, page: _Page) -> str:
    lead = _LEAD_NUMBER_RE.match(text)
    if lead and page.is_numbering(lead.group(1), text[lead.end() :]):
        text = text[lead.end() :]
    spans = [
        m
        for m in _INLINE_NUMBER_RE.finditer(text)
        if page.is_numbering(m.group(1), text[m.end() :])
    ]
    for match in reversed(spans):
        text = f"{text[: match.start()]} {text[match.end() :]}"
    text = re.sub(r"\s{1,3},", ",", " ".join(text.split()))
    return re.sub(r",\s{0,2},", ",", text)


def _clean(claim: Claim, page: _Page) -> Claim:
    text = _ANNOTATION_RE.sub("", claim.text)
    text = _strip_numbers(text, page)
    for first, second in page.dashed:
        text = text.replace(f"{first}, {second}", f"{first} {second}")
    text = text.strip(" ,;")
    if claim.kind == "item":
        text = _ITEM_JOIN_RE.sub("", text)
    if text and text[:1].islower() and text[1:2].islower() and "@" not in text[:40]:
        text = text[:1].upper() + text[1:]
    return claim if text == claim.text else replace(claim, text=text or claim.text)


def _split_labels(shown: list[Claim], page: _Page) -> list[Claim]:
    """An item joining two card labels ("Cryptography, Logging & Monitoring"):
    one item per label."""
    labels = {label for _, label in page.numbered}
    out: list[Claim] = []
    for claim in shown:
        pieces = [p.strip() for p in claim.text.split(", ")]
        if (
            claim.kind == "item"
            and len(pieces) > 1
            and all(_norm(p) in labels for p in pieces)
        ):
            out.extend(replace(claim, text=p) for p in pieces)
        else:
            out.append(claim)
    return out


def _bare_heading(claim: Claim, page: _Page) -> tuple[int, str] | None:
    """(source, body) of the card whose heading ``claim`` only repeats."""
    words = claim.text.rstrip(_TERMINAL).split()
    if (
        claim.kind != "sentence"
        or len(words) > _MAX_HEADING_WORDS
        or _FINITE_RE.search(claim.text)
    ):
        return None
    return page.headings.get(_norm(claim.text))


def _steps(shown: list[Claim]) -> list[Claim]:
    """Runs of "Step n — X" rows: items "X" (the order keeps the numbers)."""
    out: list[Claim] = []
    index = 0
    while index < len(shown):
        end = index
        while end < len(shown):
            found = _STEP_RE.match(shown[end].text)
            if found is None or len(found.group(1).split()) > _MAX_STEP_WORDS:
                break
            end += 1
        if end - index >= _MIN_STEP_RUN:
            for claim in shown[index:end]:
                label = _STEP_RE.match(claim.text).group(1).strip(" ,;.")  # type: ignore[union-attr]
                out.append(replace(claim, text=label, kind="item"))
            index = end
        else:
            out.append(shown[index])
            index += 1
    return out


def _restore_steps(shown: list[Claim], originals: Sequence[Claim]) -> list[Claim]:
    """A step run of the originals (some rows lost as "fragments", a trailing
    sentence turned into a step): shown whole, in order, as items."""
    runs = _steps(list(originals))
    step_items = [
        c
        for c, o in zip(runs, [*originals][: len(runs)], strict=False)
        if c.kind == "item" and _STEP_RE.match(o.text)
    ]
    if len(runs) != len(originals) or len(step_items) < _MIN_STEP_RUN:
        return shown
    quotes = {(c.source, c.quote) for c in step_items}
    if not any((c.source, c.quote) in quotes for c in shown):
        return shown
    kinds = {(c.source, c.quote): c.kind for c in originals}
    out: list[Claim] = []
    placed = False
    for claim in shown:
        if (claim.source, claim.quote) in quotes:
            if not placed:
                out.extend(step_items)
                placed = True
            continue
        kind = kinds.get((claim.source, claim.quote), claim.kind)
        out.append(claim if kind == claim.kind else replace(claim, kind=kind))
    return out


def _rejoin_series(shown: list[Claim], originals: Sequence[Claim]) -> list[Claim]:
    """A sentence split into a lead and bullets where it is no list ("an
    ISMS that:" + "Includes policy", "Depending on tier"): the sentence."""
    whole = {(c.source, c.quote): c for c in originals if c.kind == "sentence"}
    out: list[Claim] = []
    index = 0
    while index < len(shown):
        lead = shown[index]
        key = (lead.source, lead.quote)
        end = index + 1
        while (
            end < len(shown)
            and shown[end].kind == "item"
            and (
                shown[end].source,
                shown[end].quote,
            )
            == key
        ):
            end += 1
        items = shown[index + 1 : end]
        original = whole.get(key)
        bad = _BAD_LEAD_RE.search(lead.text) or any(
            _BAD_ITEM_RE.search(c.text) for c in items
        )
        if items and original is not None and lead.kind == "sentence" and bad:
            out.append(original)
            index = end
            continue
        out.append(lead)
        index += 1
    return out


def _leads(shown: list[Claim]) -> list[Claim]:
    """A lead cut before its complement: ":" before a list, else dropped."""
    out: list[Claim] = []
    for index, claim in enumerate(shown):
        text = claim.text.rstrip()
        if claim.kind != "sentence" or _DANGLING_RE.search(text) is None:
            out.append(claim)
            continue
        listed = index + 1 < len(shown) and shown[index + 1].kind == "item"
        if listed:
            out.append(replace(claim, text=f"{text.rstrip(' .:')}:"))
        elif len(shown) == 1:
            out.append(claim)
    return out


def shape_reply(
    shown: list[Claim], originals: Sequence[Claim], sources: Sequence[SourceChunk]
) -> list[Claim]:
    """The claims to render, with the answer-shape repairs above applied."""
    if not shown:
        return shown
    page = _Page(sources)
    shown = _restore_steps(shown, [_clean(c, page) for c in originals])
    shown = _rejoin_series(shown, originals)
    shown = _split_labels(_steps([_clean(c, page) for c in shown]), page)
    out: list[Claim] = []
    for claim in shown:
        card = _bare_heading(claim, page)
        others = [c for c in shown if c is not claim and _bare_heading(c, page) is None]
        if card is None:
            out.append(claim)
        elif not others and len(card[1].split()) <= _MAX_BODY_WORDS:
            source, body = card
            label = claim.text.rstrip(_TERMINAL)
            text = f"{label}: {body}"
            out.append(replace(claim, text=text, source=source, kind="sentence"))
    return _leads(out or shown)
