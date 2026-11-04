"""One canonical form of the visitor's text, made before any gate runs.

Every deterministic gate (pre-checks, small-talk, intent) and retrieval see
``normalise_input(raw)``; the raw text is kept for audit only. The function
is pure, length-capped and uses only bounded quantifiers, so it is safe on
hostile input (no polynomial ReDoS).

What it does, in order:

- NFKC (full-width ``ｗｈａｔ`` -> ``what``, ligatures, compatibility forms).
- Drops format/zero-width characters (Unicode ``Cf``), turns other control
  characters into spaces.
- Strips wrapper markup: simple HTML tags, code fences, backticks, ``**``/
  ``__`` emphasis, straight/smart quotes around the message, a leading
  ``¿``/``¡``.
- ``zero_trust`` -> ``zero trust`` (an underscore between two words).
- Folds accents on Latin letters (``Zéro`` -> ``Zero``; the knowledge base
  is English); Cyrillic, Greek, CJK and other scripts are kept as typed.
- Maps confusable homoglyphs (Cyrillic/Greek look-alikes) to Latin, only in
  tokens that mix scripts, so real Cyrillic/Greek text is left alone.
- Collapses spaces, tabs and NBSPs; keeps one ``\\n`` between lines so a
  multi-part message keeps its separators.
"""

from __future__ import annotations

import re
import unicodedata

# The API caps queries far below this; capped again before any regex work.
MAX_NORMALISE_CHARS = 4000

# Cyrillic/Greek letters that look like a Latin letter (lower and upper case).
_CONFUSABLE_PAIRS = {
    # Cyrillic lower
    "\u0430": "a",
    "\u0435": "e",
    "\u043e": "o",
    "\u0440": "p",
    "\u0441": "c",
    "\u0443": "y",
    "\u0445": "x",
    "\u0456": "i",
    "\u0458": "j",
    "\u0455": "s",
    "\u04bb": "h",
    "\u043a": "k",
    "\u043c": "m",
    "\u0442": "t",
    "\u0432": "b",
    "\u043d": "h",
    "\u0501": "d",
    "\u0261": "g",
    # Cyrillic upper
    "\u0410": "A",
    "\u0412": "B",
    "\u0415": "E",
    "\u041a": "K",
    "\u041c": "M",
    "\u041d": "H",
    "\u041e": "O",
    "\u0420": "P",
    "\u0421": "C",
    "\u0422": "T",
    "\u0423": "Y",
    "\u0425": "X",
    "\u0406": "I",
    "\u0408": "J",
    "\u0405": "S",
    # Greek
    "\u03b1": "a",
    "\u03bf": "o",
    "\u03bd": "v",
    "\u03c1": "p",
    "\u03b9": "i",
    "\u03ba": "k",
    "\u03c4": "t",
    "\u03c5": "u",
    "\u03b5": "e",
    "\u03b7": "n",
    "\u03b2": "b",
    "\u03c7": "x",
    "\u03b6": "z",
    "\u03f2": "c",
    "\u0391": "A",
    "\u0392": "B",
    "\u0395": "E",
    "\u0396": "Z",
    "\u0397": "H",
    "\u0399": "I",
    "\u039a": "K",
    "\u039c": "M",
    "\u039d": "N",
    "\u039f": "O",
    "\u03a1": "P",
    "\u03a4": "T",
    "\u03a5": "Y",
    "\u03a7": "X",
}
_CONFUSABLES = str.maketrans(_CONFUSABLE_PAIRS)

_HTML_TAG_RE = re.compile(r"</?[A-Za-z][A-Za-z0-9]{0,10}(?:\s[^<>]{0,200})?/?>")
_FENCE_RE = re.compile(r"`{3}[A-Za-z0-9_+-]{0,20}")
# Markdown emphasis only: "**x**", "*x*", "__x__". "2*3" and "2 * 3" stay.
_EMPHASIS_RE = re.compile(
    r"\*{2,3}|(?:(?<=\s)|^)\*(?=[^\s*])|(?<=[^\s*])\*(?=\s|$|[?.!,])"
    r"|(?<![A-Za-z0-9])_{2,3}|_{2,3}(?![A-Za-z0-9])"
)
_WORD_UNDERSCORE_RE = re.compile(r"(?<=[^\W_])_{1,3}(?=[^\W_])")
_LEADING_INVERTED_RE = re.compile(r"^[\u00bf\u00a1]{1,3}\s{0,3}")
_INNER_INVERTED_RE = re.compile(r"(?<=\s)[\u00bf\u00a1]{1,3}")
_HSPACE_RE = re.compile(r"[^\S\n]{1,50}")
_NEWLINES_RE = re.compile(r"\s{0,50}\n\s{0,50}")
_TOKEN_RE = re.compile(r"\S{1,200}")
_SPACE_BEFORE_PUNCT_RE = re.compile(r" {1,3}(?=[?!.,](?:\s|$))")

# Quote characters that wrap a whole message ("…", “…”, ‘…’, «…», `…`).
_WRAP_QUOTES = "\"'`\u201c\u201d\u2018\u2019\u00ab\u00bb\u201e\u201a"
_SMART_QUOTES = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u00ab": '"',
        "\u00bb": '"',
    }
)


def strip_format_chars(text: str, keep: str = "\n") -> str:
    """Drop zero-width/format chars; control chars not in ``keep`` become a space."""
    out: list[str] = []
    for ch in text:
        category = unicodedata.category(ch)
        if category == "Cf":
            continue
        if category == "Cc" and ch not in keep:
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def _script(ch: str) -> str:
    """'latin', 'cyrillic', 'greek' or '' for a letter (by its Unicode name)."""
    name = unicodedata.name(ch, "")
    for script in ("LATIN", "CYRILLIC", "GREEK"):
        if name.startswith(script):
            return script
    return ""


def _fix_mixed_script(token: str) -> str:
    """Map look-alikes to Latin only in a token mixing Latin with Cyrillic/Greek."""
    scripts = {_script(ch) for ch in token if ch.isalpha()}
    scripts.discard("")
    if "LATIN" not in scripts or len(scripts) < 2:  # noqa: PLR2004
        return token
    return token.translate(_CONFUSABLES)


def _strip_wrappers(text: str) -> str:
    """Remove markup a visitor pasted around the question."""
    text = _HTML_TAG_RE.sub(" ", text)
    text = _FENCE_RE.sub(" ", text)
    text = _EMPHASIS_RE.sub("", text)
    text = text.replace("`", "")
    text = _WORD_UNDERSCORE_RE.sub(" ", text)
    text = _LEADING_INVERTED_RE.sub("", text)
    text = _INNER_INVERTED_RE.sub("", text)
    stripped = text.strip()
    # Quotes around the whole message: "what is CIS" -> what is CIS
    while (
        len(stripped) >= 2  # noqa: PLR2004
        and stripped[0] in _WRAP_QUOTES
        and stripped[-1] in _WRAP_QUOTES
    ):
        stripped = stripped[1:-1].strip()
    return stripped


def _collapse_whitespace(text: str) -> str:
    text = _HSPACE_RE.sub(" ", text)
    text = _NEWLINES_RE.sub("\n", text)
    return _SPACE_BEFORE_PUNCT_RE.sub("", text).strip()


def _fold_latin_accents(text: str) -> str:
    """'Whät is Zéro Trust' -> 'What is Zero Trust'; other scripts unchanged."""
    if text.isascii():
        return text
    out: list[str] = []
    for ch in text:
        if ch.isascii() or _script(ch) != "LATIN":
            out.append(ch)
            continue
        base = "".join(
            c
            for c in unicodedata.normalize("NFKD", ch)
            if unicodedata.category(c) != "Mn"
        )
        out.append(base or ch)
    return "".join(out)


def normalise_input(text: str) -> str:
    """The canonical text every gate and retrieval use (see module docstring)."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text[:MAX_NORMALISE_CHARS])
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = strip_format_chars(text)
    text = text.translate(_SMART_QUOTES)
    text = _TOKEN_RE.sub(lambda m: _fix_mixed_script(m.group(0)), text)
    text = _fold_latin_accents(text)
    text = _strip_wrappers(text)
    return _collapse_whitespace(text)


def fold_accents(text: str) -> str:
    """'Zéro Trüst' -> 'Zero Trust' (for letter-shape heuristics only)."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
