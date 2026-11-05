"""Knowledge clean-up: Markdown (uploaded or converted from .docx) -> on-page copy.

Runs inside ``normalize_markdown`` for every entry point, before PII
redaction and chunking, so the embedded text carries meaning, not markup:

1. Documents with no real heading structure (typical .docx exports) get
   their standalone bold lines promoted to headings.
2. Bracketed label sections from website snapshots: UI-only ones
   (sidebars, forms, booking widgets, structured data) are dropped whole;
   visual labels ("Hero", "Closing CTA", infographics) are removed and their
   copy folds into the parent section; other labels become plain headings.
3. Line noise: CTA buttons, ``_(...)_`` UI notes, ``_[Editor note]_``
   blocks, form-field specs, template variables, source-code references,
   images, link targets, HTML tags, backslash escapes, bold/italic markers.
4. Tables become one sentence per row (``Row: Header: value; ...``, check
   marks -> "included in ..."), so a row keeps its meaning on its own.
5. Repeated paragraphs inside one document are kept once.

Only plain string operations and bounded patterns run here: the input is
uploaded text.
"""

from __future__ import annotations

ARROW = "→"
CHECK_MARKS = frozenset({"✓", "✔", "✅", "yes", "y", "x"})
CTA_MARKER = "**[CTA]**"
LINK_MARKER = "**[Link]**"
BULLETS = ("- ", "* ", "+ ")
NOTE_OPEN, NOTE_CLOSE = "_(", ")_"
EDITOR_OPEN, EDITOR_CLOSE = "_[", "]_"
LINK_TARGET_STARTS = ("/", "http://", "https://", "#", "mailto:", "tel:")
LABEL_PREFIXES = ("Section:", "Box:")
ILLUSTRATIVE_WORDS = ("illustrative", "sample", "example")
ILLUSTRATIVE_NOTE = "(Illustrative example only.)"
MIN_DEDUPE_CHARS = 40
MIN_HEADINGS_FOR_STRUCTURE = 2
MAX_TEMPLATE_VAR_CHARS = 40
MAX_HEADING_LEVEL = 6
BOLD_WRAP_CHARS = 4
MIN_TABLE_ROWS = 2
MIN_REPEATED_LIST_ITEMS = 3

# Label sections that describe UI only: dropped with everything under them.
DROP_SECTIONS = (
    "structured data",
    "contents sidebar",
    "sidebar",
    "booking card — calendar",
    "time slots",
    "contact form",
    "strategy cards grid",
    "ui messages",
    "modal footer",
)
# Visual labels whose copy belongs to the parent section (label removed).
MERGE_SECTIONS = (
    "hero",
    "closing cta",
    "pop-up modal",
    "interactive panel",
    "banner",
    "framework strip",
    "infographic",
    "journey progress banner",
    "quick links",
)
# A named diagram label ("[Hero diagram: Orchestration cycle]") is its own
# section; a positional one ("[Infographic: hero, right-hand side]") merges.
VISUAL_KINDS = ("hero diagram", "diagram", "infographic")
POSITIONAL_WORDS = ("hero", "side", "left", "right", "top", "bottom")
# A UI note marking a mobile-only re-rendering of copy shown above it: the
# line (a toggle label) is dropped; the repeated list is de-duplicated.
MOBILE_ONLY_NOTE = "_(mobile only"
# A sub-heading (this level or deeper) with no copy of its own is itself the
# copy (e.g. a list of key points written as headings): kept as a bullet.
MIN_BULLET_HEADING_LEVEL = 3
# A line containing any of these is a form/UI specification, not copy.
UI_SPEC_MARKERS = (
    "placeholder:",
    "button text:",
    "(required)",
    "textarea",
    "select, placeholder",
    "swipe to see",
    "selected by default",
    "click to select",
)
# Label prefixes whose value is on-page copy ("Eyebrow: X" -> "X").
VALUE_PREFIXES = ("Eyebrow:", "Badge:", "Caption:", "Label:", "Stat:")
# "Stat (shown only for this item): 24/7 ..." -> "24/7 ...".
VALUE_PREFIXES_WITH_NOTE = ("Stat (", "Caption (", "Badge (")
# A label line ("Centre hub:", "Selector items:") that narrates the UI.
UI_LABEL_WORDS = (
    "hub",
    "caption bar",
    "selector",
    "stages around",
    "tiles",
    "cards",
    "infographic",
    "diagram",
    "panel",
    "carousel",
    "accordion",
)
LEADING_MARKS = ("✓ ", "✔ ", "✅ ")
SOURCE_CODE_MARKERS = ("src/", ".tsx", ".ts`", "JSON-LD")
HTML_TAGS = ("<br>", "<br/>", "<br />", "<b>", "</b>", "<strong>", "</strong>")
ESCAPABLE = "\\`*_{}[]()#+-.!|>~"


# --- inline helpers -----------------------------------------------------------


def _remove_spans(line: str, opener: str, closer: str, keep=None) -> str:
    """Remove ``opener ... closer`` spans; ``keep(inner)`` may return a replacement."""
    out, pos = [], 0
    while (start := line.find(opener, pos)) >= 0:
        end = line.find(closer, start + len(opener))
        if end < 0:
            break
        out.append(line[pos:start])
        inner = line[start + len(opener) : end]
        if keep is not None and (replacement := keep(inner)):
            out.append(replacement)
        pos = end + len(closer)
    out.append(line[pos:])
    return "".join(out)


def _note_replacement(inner: str) -> str | None:
    """An illustrative/sample note is kept as ``(its own words)``: it tells
    the reader the figures that follow are a sample."""
    lowered = inner.lower()
    if not any(w in lowered for w in ILLUSTRATIVE_WORDS):
        return None
    words = " ".join(inner.split()).rstrip(".")
    return f"({words})" if words else ILLUSTRATIVE_NOTE


def _drop_images(line: str) -> str:
    while (start := line.find("![")) >= 0:
        close = line.find("](", start)
        end = line.find(")", close + 2) if close >= 0 else -1
        if end < 0:
            break
        line = line[:start] + line[end + 1 :]
    return line


def _unlink(line: str) -> str:
    """``[label](url)`` -> ``label``."""
    out, pos = [], 0
    while (start := line.find("[", pos)) >= 0:
        mid = line.find("](", start)
        end = line.find(")", mid + 2) if mid >= 0 else -1
        if mid < 0 or end < 0 or "\n" in line[start:end]:
            break
        out.append(line[pos:start])
        out.append(line[start + 1 : mid])
        pos = end + 1
    out.append(line[pos:])
    return "".join(out)


def _drop_link_target(line: str) -> str:
    """``Zero Trust Services -> /zero-trust`` -> ``Zero Trust Services``."""
    arrow = line.rfind(ARROW)
    if arrow >= 0 and line[arrow + 1 :].strip().startswith(LINK_TARGET_STARTS):
        return line[:arrow].rstrip()
    return line


def _unescape(line: str) -> str:
    out, index = [], 0
    while index < len(line):
        char = line[index]
        if char == "\\" and index + 1 < len(line) and line[index + 1] in ESCAPABLE:
            out.append(line[index + 1])
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _strip_emphasis(line: str) -> str:
    return line.replace("**", "").replace("__", "")


def _list_item(stripped: str) -> str:
    for bullet in BULLETS:
        if stripped.startswith(bullet):
            return stripped[len(bullet) :].lstrip()
    return stripped


def _is_template_line(line: str) -> bool:
    """A UI string with a ``{variable}`` placeholder (``{time}``, ``{n}``)."""
    start = line.find("{")
    end = line.find("}", start + 1) if start >= 0 else -1
    return 0 <= start < end and 0 < end - start <= MAX_TEMPLATE_VAR_CHARS


def markdown_heading(line: str) -> tuple[int, str] | None:
    """``(level, title)`` for an ATX heading line (``## Title``), else ``None``."""
    stripped = line.strip()
    hashes = len(stripped) - len(stripped.lstrip("#"))
    if 1 <= hashes <= MAX_HEADING_LEVEL and stripped[hashes : hashes + 1] == " ":
        return hashes, stripped[hashes + 1 :].strip()
    return None


# --- line rules ---------------------------------------------------------------


def _clean_line(raw: str) -> str | None:
    """The copy on one line, or ``None`` when the line is not copy."""
    line = _remove_spans(raw, EDITOR_OPEN, EDITOR_CLOSE)
    line = _remove_spans(line, NOTE_OPEN, NOTE_CLOSE, keep=_note_replacement)
    item = _list_item(line.strip())
    is_mobile_copy = raw.lstrip().lower().startswith(MOBILE_ONLY_NOTE)
    if is_mobile_copy or item.startswith(CTA_MARKER):
        return None
    lowered = line.lower()
    if any(marker in lowered for marker in UI_SPEC_MARKERS):
        return None
    if any(marker in line for marker in SOURCE_CODE_MARKERS):
        return None
    if _is_template_line(line):
        return None
    line = line.replace(LINK_MARKER, "")
    for tag in HTML_TAGS:
        line = line.replace(tag, " " if tag.startswith("<br") else "")
    line = _drop_link_target(_unlink(_drop_images(line)))
    line = _strip_emphasis(_unescape(line)).rstrip()
    stripped = line.strip()
    if _is_ui_label(stripped):
        return None
    for prefix in VALUE_PREFIXES:
        if stripped.startswith(prefix):
            line = stripped.removeprefix(prefix)
            break
    for prefix in VALUE_PREFIXES_WITH_NOTE:
        if stripped.startswith(prefix) and "):" in stripped:
            line = stripped.split("):", 1)[1]
            break
    for mark in LEADING_MARKS:
        line = line.replace(mark, "", 1) if line.lstrip().startswith(mark) else line
    return _tidy_spaces(line)


def _is_ui_label(stripped: str) -> bool:
    """A bare ``Something:`` line that only names a UI element."""
    if not stripped.endswith(":") or _list_item(stripped) != stripped:
        return False
    lowered = stripped.lower()
    return any(word in lowered for word in UI_LABEL_WORDS)


def _tidy_spaces(line: str) -> str | None:
    """Collapse the gaps removed notes leave; keep list indentation only."""
    body = line.strip()
    if not body:
        return ""
    item = _list_item(body)
    if not item:
        return None  # an empty list item left behind
    indent = line[: len(line) - len(line.lstrip())]
    is_list = item != body
    marker = body[: len(body) - len(item)] if item != body else ""
    words = " ".join(item.split())
    number, _, rest = body.partition(" ")
    if number[:1].isdigit() and number.endswith((".", ")")):
        return f"{indent}{number} {' '.join(rest.split())}".rstrip()
    return (
        f"{indent if is_list else ''}{marker.strip()} {words}".strip(" ")
        if marker
        else words
    )


# --- headings -------------------------------------------------------------------


def _label(text: str) -> str | None:
    """The label inside ``[Section: Hero]`` / ``[Hero stat cards]:``, else ``None``."""
    text = text.strip().rstrip(":").strip()
    if not (text.startswith("[") and text.endswith("]")):
        return None
    label = text[1:-1].strip()
    for prefix in LABEL_PREFIXES:
        label = label.removeprefix(prefix).strip()
    return label


def _visual_title(label: str) -> str | None:
    """``Hero diagram: Orchestration cycle`` -> ``Orchestration cycle``: a
    named diagram is a section of its own; a positional one ("hero,
    right-hand side") is not."""
    kind, colon, name = label.partition(":")
    name = name.strip()
    lowered = name.lower()
    if not colon or kind.strip().lower() not in VISUAL_KINDS or not name:
        return None
    if any(word in lowered for word in POSITIONAL_WORDS):
        return None
    return name


def _section_action(title: str) -> tuple[str, str]:
    """(``drop`` | ``merge`` | ``keep``, heading text) for one heading."""
    label = _label(title)
    plain = _strip_emphasis(_unescape(label if label is not None else title)).strip()
    lowered = plain.lower()
    if any(lowered.startswith(name) for name in DROP_SECTIONS):
        return "drop", plain
    if label is not None and (visual := _visual_title(plain)):
        return "keep", visual
    if label is not None and any(lowered.startswith(n) for n in MERGE_SECTIONS):
        return "merge", plain
    return "keep", plain


def _promote_bold_headings(lines: list[str]) -> list[str]:
    """No real headings (a .docx without heading styles): bold-only lines
    become ``##`` headings so the document keeps its sections."""
    if sum(1 for line in lines if markdown_heading(line)) >= MIN_HEADINGS_FOR_STRUCTURE:
        return lines
    out = []
    for line in lines:
        stripped = line.strip()
        inner = stripped[2:-2].strip() if len(stripped) > BOLD_WRAP_CHARS else ""
        if stripped.startswith("**") and stripped.endswith("**") and "**" not in inner:
            out.append(f"## {inner}")
        else:
            out.append(line)
    return out


# --- tables ---------------------------------------------------------------------


def _cells(row: str) -> list[str]:
    row = row.strip().removeprefix("|").removesuffix("|")
    return [_strip_emphasis(_unescape(cell)).strip() for cell in row.split("|")]


def _is_separator(row: str) -> bool:
    body = row.replace("|", "").replace(":", "").replace("-", "").strip()
    return not body and "-" in row


def _table_sentences(rows: list[str]) -> list[str]:
    """One self-contained sentence per data row."""
    header = _cells(rows[0])
    body = [_cells(r) for r in rows[1:] if not _is_separator(r)]
    check_table = any(
        cell.lower() in CHECK_MARKS for row in body for cell in row[1:] if cell
    )
    sentences = []
    for cells in body:
        label = cells[0] if cells else ""
        values = list(zip(header[1:], cells[1:], strict=False))
        if check_table and all(not v or v.lower() in CHECK_MARKS for _, v in values):
            yes = [h for h, v in values if v]
            no = [h for h, v in values if not v]
            text = f"{label}: included in {', '.join(yes) or 'none'}"
            if no:
                text += f"; not included in {', '.join(no)}"
        else:
            pairs = "; ".join(f"{h}: {v}" for h, v in values if v)
            text = f"{label}: {pairs}" if label else pairs
        sentences.append(f"- {text}.")
    return sentences


def _linearise_tables(lines: list[str]) -> list[str]:
    out, block = [], []
    for line in [*lines, ""]:
        if line.strip().startswith("|"):
            block.append(line)
            continue
        if block:
            out.extend(
                _table_sentences(block) if len(block) >= MIN_TABLE_ROWS else block
            )
            block = []
        out.append(line)
    return out[:-1]


# --- paragraphs -----------------------------------------------------------------


def _dedupe_paragraphs(lines: list[str]) -> list[str]:
    """Drop a paragraph (or list item) already seen earlier in the document."""
    seen: set[str] = set()
    out = []
    for line in lines:
        key = " ".join(_list_item(line.strip()).lower().split())
        if len(key) >= MIN_DEDUPE_CHARS and not markdown_heading(line):
            if key in seen:
                continue
            seen.add(key)
        out.append(line)
    return out


def _item_key(line: str) -> str | None:
    """Words of a list item without its marker or numbering (``01 Onboard —
    X`` and ``1. Onboard: X`` match), or ``None`` for a non-list line."""
    stripped = line.strip()
    number = stripped.partition(" ")[0]
    is_numbered = number[:1].isdigit() and number.endswith((".", ")"))
    if _list_item(stripped) == stripped and not is_numbered:
        return None
    words = "".join(c if c.isalnum() else " " for c in stripped.lower()).split()
    while words and words[0].isdigit():
        words.pop(0)
    return " ".join(words)


def _dedupe_section_lists(lines: list[str]) -> list[str]:
    """Drop a list repeated item-for-item within the same section (a desktop
    and a mobile rendering of one list); short items count here, unlike in
    ``_dedupe_paragraphs``, because the whole list must match."""
    out: list[str] = []
    seen: set[tuple[str, ...]] = set()
    block: list[str] = []
    for line in [*lines, ""]:
        if _item_key(line) is not None:
            block.append(line)
            continue
        if block:
            keys = tuple(_item_key(item) or "" for item in block)
            if len(block) < MIN_REPEATED_LIST_ITEMS or keys not in seen:
                out.extend(block)
            seen.add(keys)
            block = []
        if markdown_heading(line):
            seen = set()
        out.append(line)
    return out[:-1]


def _collapse_blank_lines(lines: list[str]) -> list[str]:
    out = []
    for line in lines:
        if not line.strip() and (not out or not out[-1].strip()):
            continue
        out.append(line)
    return out


def _drop_empty_sections(lines: list[str]) -> list[str]:
    """A heading whose section holds no copy before the next heading of the
    same or a shallower level: a sub-heading is copy itself (kept as a
    bullet under its parent); a top-level label is dropped."""
    out = []
    for index, line in enumerate(lines):
        found = markdown_heading(line)
        if found is None:
            out.append(line)
            continue
        level = found[0]
        has_copy = False
        for later in lines[index + 1 :]:
            nxt = markdown_heading(later)
            if nxt is not None:
                if nxt[0] <= level:
                    break
                has_copy = True  # a sub-section with its own copy
                break
            if later.strip():
                has_copy = True
                break
        if has_copy:
            out.append(line)
        elif level >= MIN_BULLET_HEADING_LEVEL:
            out.append(f"- {found[1]}")
    return out


SAMPLE_LINE_STARTS = ("sample ",)
SAMPLE_SECTION_TEXT = (
    "This section shows an illustrative sample to explain the report, not real results."
)


def _is_sample_marker(line: str) -> bool:
    """A whole-line illustrative note ``(Illustrative sample score card)`` or
    a ``Sample …`` line."""
    text = line.strip()
    lowered = text.lower()
    if SAMPLE_SECTION_TEXT in text:  # already framed: cleaning is idempotent
        return True
    if text.startswith("(") and text.endswith(")"):
        return any(word in lowered for word in ILLUSTRATIVE_WORDS)
    return lowered.startswith(SAMPLE_LINE_STARTS)


def sample_framing(marker: str) -> str:
    """The marker's own words plus the fixed sample sentence, e.g.
    ``Illustrative sample score card. This section shows …``."""
    if SAMPLE_SECTION_TEXT in marker:
        return marker.strip()
    words = marker.strip().strip("()").strip().rstrip(".")
    return f"{words}. {SAMPLE_SECTION_TEXT}" if words else SAMPLE_SECTION_TEXT


def _neutralise_samples(lines: list[str]) -> list[str]:
    """A section showing mock data (a "Sample …" line or an illustrative note)
    keeps its heading, but its numbers are replaced by one plain sentence so
    demo figures can never be quoted as facts."""
    out: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        found = markdown_heading(line)
        if found is None:
            out.append(line)
            index += 1
            continue
        level = found[0]
        end = index + 1
        while end < len(lines):
            nxt = markdown_heading(lines[end])
            if nxt is not None and nxt[0] <= level:
                break
            end += 1
        # Only the section's OWN copy (before its first sub-heading) decides;
        # a sample deep inside must not wipe its parent.
        own: list[str] = []
        for s in lines[index + 1 : end]:
            if markdown_heading(s) is not None:
                break
            own.append(s)
        marker = next((s for s in own if _is_sample_marker(s)), None)
        if marker is not None:
            out.extend([line, "", sample_framing(marker), ""])
            index = end
            continue
        out.append(line)
        index += 1
    return out


def _fold_point(out: list[str], level: int) -> int | None:
    """Where a merged label's copy goes: the end of its parent's own text.

    The parent is the last heading shallower than ``level``; when a deeper
    section was emitted after it (e.g. a hero diagram), appending would file
    the copy under that section, so it is inserted before it instead.
    ``None`` means append.
    """
    parent = -1
    for index in range(len(out) - 1, -1, -1):
        found = markdown_heading(out[index])
        if found is not None and found[0] < level:
            parent = index
            break
    for index in range(parent + 1, len(out)):
        if markdown_heading(out[index]) is not None:
            return index
    return None


def clean_markdown(text: str) -> str:
    """The cleaned, meaning-preserving Markdown for one document."""
    lines = _promote_bold_headings(text.split("\n"))
    out: list[str] = []
    drop_level: int | None = None
    fold_at: int | None = None
    for raw in lines:
        found = markdown_heading(raw)
        if found is not None:
            level, title = found
            if drop_level is not None and level > drop_level:
                continue
            drop_level = fold_at = None
            action, plain = _section_action(title)
            if action == "drop":
                drop_level = level
            elif action == "merge":
                fold_at = _fold_point(out, level)
            elif plain:
                out.append(f"{'#' * level} {plain}")
            continue
        if drop_level is not None:
            continue
        cleaned = _clean_line(raw)
        if cleaned is None:
            continue
        if fold_at is None:
            out.append(cleaned)
        else:
            out.insert(fold_at, cleaned)
            fold_at += 1
    out = _linearise_tables(out)
    out = _neutralise_samples(out)
    out = _dedupe_paragraphs(_dedupe_section_lists(out))
    # Removing an empty sub-section can empty its parent: repeat until stable.
    while (pruned := _drop_empty_sections(out)) != out:
        out = pruned
    return "\n".join(_collapse_blank_lines(out)).strip() + "\n"
