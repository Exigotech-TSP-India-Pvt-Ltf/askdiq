"""Section-tree chunking: one chunk per self-contained section of a page.

The cleaned Markdown (see ``app.ingestion.cleanup``) is read as a heading
tree. A section is kept whole with everything under it while it fits
``max_chars``, so a tier, an FAQ answer, a stage list or a definition stays
in one chunk with its own heading. A larger section splits at its
sub-headings first; only a single oversized leaf is packed by paragraph,
then by list item / line, then by sentence. Neighbouring pieces under the
same parent that are too small to stand alone are merged. A section made of
three or more sibling cards (sub-headings with their own copy) is always
split: one chunk per card, plus the intro naming the cards.

Every chunk starts with its breadcrumb (``Page › Section › Sub-section``),
so the embedded text and the prompt both say what the chunk is about, and
the metadata carries ``page_title``, ``section_path``, ``section_heading``,
``anchor`` and ``content_type``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath

from app.chunking.base import ChunkingStrategy, ChunkResult
from app.ingestion.cleanup import markdown_heading
from app.knowledge.links import slugify_anchor

BREADCRUMB_SEPARATOR = f" {chr(0x203A)} "  # "Page > Section > Sub-section" (U+203A)
LIST_MARKERS = ("- ", "* ", "+ ")
SENTENCE_END = ". "
LIST_SHARE = 0.5
MIN_CARDS = 3
# A card's own copy must be a sentence or so; one-phrase cards ("Autonomous:
# Human-in-loop") read better together, as one list-like section.
MIN_CARD_CHARS = 40


@dataclass
class _Node:
    level: int
    title: str
    lines: list[str] = field(default_factory=list)
    children: list[_Node] = field(default_factory=list)


@dataclass
class _Piece:
    """Text for one chunk: ``path`` names its section; ``title`` is shown
    inline only when the piece is merged into a parent."""

    path: tuple[str, ...]
    title: str
    body: str
    alone: bool = False  # a card-set intro: never merged with siblings

    def text(self, with_title: bool) -> str:
        if with_title and self.title:
            return f"{self.title}\n{self.body}".strip()
        return self.body.strip()


def _parse(text: str) -> _Node:
    root = _Node(0, "")
    stack = [root]
    for line in text.split("\n"):
        found = markdown_heading(line)
        if found is None:
            stack[-1].lines.append(line)
            continue
        level, title = found
        while stack[-1].level >= level:
            stack.pop()
        node = _Node(level, title)
        stack[-1].children.append(node)
        stack.append(node)
    return root


def _lift_page_title(root: _Node) -> str:
    """A single top-level heading is the page title: its body becomes the
    page's own text and its sub-sections the page's sections."""
    if len(root.children) == 1 and not "".join(root.lines).strip():
        page = root.children[0]
        root.lines, root.children = page.lines, page.children
        return page.title
    return ""


def _render(node: _Node) -> str:
    """The node's own text plus every descendant with its heading line."""
    parts = ["\n".join(node.lines).strip()]
    for child in node.children:
        child_text = _render(child)
        parts.append(f"{child.title}\n{child_text}".strip())
    return "\n\n".join(p for p in parts if p).strip()


def _is_card_set(node: _Node) -> bool:
    """A section made of >= ``MIN_CARDS`` sibling sub-sections that each
    carry their own copy (cards, stages, carousel items): each card retrieves
    best alone, so it is never merged with its siblings."""
    return len(node.children) >= MIN_CARDS and all(
        len(" ".join(child.lines).strip()) >= MIN_CARD_CHARS for child in node.children
    )


def _holds_card_set(node: _Node) -> bool:
    """A card set somewhere below ``node``: it is split even when the whole
    of ``node`` would fit in one chunk."""
    return any(_is_card_set(c) or _holds_card_set(c) for c in node.children)


class SectionTreeChunker(ChunkingStrategy):
    name = "section_tree"

    def __init__(self, max_chars: int = 1200, min_chars: int = 250) -> None:
        self.max_chars = max_chars
        self.min_chars = min_chars

    # --- splitting -------------------------------------------------------------

    def _split_sentences(self, line: str) -> list[str]:
        pieces, current = [], ""
        for sentence in line.split(SENTENCE_END):
            candidate = f"{current}{SENTENCE_END}{sentence}" if current else sentence
            if current and len(candidate) > self.max_chars:
                pieces.append(current.strip())
                current = sentence
            else:
                current = candidate
        if current.strip():
            pieces.append(current.strip())
        return pieces

    def _units(self, text: str) -> list[str]:
        """Paragraphs; an oversized one falls back to lines, then sentences."""
        units: list[str] = []
        for raw_paragraph in text.split("\n\n"):
            paragraph = raw_paragraph.strip()
            if not paragraph:
                continue
            if len(paragraph) <= self.max_chars:
                units.append(paragraph)
                continue
            for line in paragraph.split("\n"):
                if len(line) <= self.max_chars:
                    units.append(line)
                else:
                    units.extend(self._split_sentences(line))
        return units

    def _pack(self, text: str) -> list[str]:
        packed, current = [], ""
        for unit in self._units(text):
            candidate = f"{current}\n\n{unit}" if current else unit
            if current and len(candidate) > self.max_chars:
                packed.append(current)
                current = unit
            else:
                current = candidate
        if current:
            packed.append(current)
        return packed

    def _emit(self, node: _Node, path: tuple[str, ...]) -> list[_Piece]:
        if path and _is_card_set(node):
            return self._emit_cards(node, path)
        whole = _render(node)
        if len(whole) <= self.max_chars and not _holds_card_set(node):
            return [_Piece(path, node.title, whole)] if whole else []
        pieces = [
            _Piece(path, node.title, part)
            for part in self._pack("\n".join(node.lines).strip())
        ]
        for child in node.children:
            pieces.extend(self._emit(child, (*path, child.title)))
        return self._merge_small(pieces, path)

    def _emit_cards(self, node: _Node, path: tuple[str, ...]) -> list[_Piece]:
        """One chunk per card, each under its own breadcrumb; the section's
        intro is a chunk of its own and names the cards (unless its copy
        already does), so "what are the …" still finds the whole set."""
        intro = "\n".join(node.lines).strip()
        titles = [child.title for child in node.children]
        if not all(title.lower() in intro.lower() for title in titles):
            intro = "\n\n".join(
                part for part in (intro, "\n".join(f"- {t}" for t in titles)) if part
            )
        pieces = [
            _Piece(path, node.title, part, alone=True) for part in self._pack(intro)
        ]
        for child in node.children:
            pieces.extend(self._emit(child, (*path, child.title)))
        return pieces

    def _merge_small(
        self, pieces: list[_Piece], parent: tuple[str, ...]
    ) -> list[_Piece]:
        """Fold a too-small piece into an adjacent *sibling* (same parent);
        the merged piece is filed under the parent, sibling titles inline.

        A question-shaped piece (FAQ entry) always stays on its own: it is
        self-contained and retrieves best alone.
        """
        merged: list[_Piece] = []
        for piece in pieces:
            if merged and self._mergeable(merged[-1], piece, parent):
                last = merged[-1]
                joined = "\n\n".join(
                    (self._inline(last, parent), self._inline(piece, parent))
                )
                if len(joined) <= self.max_chars:
                    merged[-1] = _Piece(parent, parent[-1] if parent else "", joined)
                    continue
            merged.append(piece)
        return merged

    def _mergeable(self, last: _Piece, piece: _Piece, parent: tuple[str, ...]) -> bool:
        if last.alone or piece.alone:
            return False
        if min(len(last.body), len(piece.body)) >= self.min_chars:
            return False
        if last.title.endswith("?") or piece.title.endswith("?"):
            return False
        depth = len(parent) + 1
        # Siblings: both sit directly under ``parent`` (or are already merged into it).
        return all(
            p.path == parent or (len(p.path) == depth and p.path[:-1] == parent)
            for p in (last, piece)
        )

    @staticmethod
    def _inline(piece: _Piece, parent: tuple[str, ...]) -> str:
        """A piece's text inside a merged parent chunk (its title once)."""
        return piece.body.strip() if piece.path == parent else piece.text(True)

    # --- metadata ----------------------------------------------------------------

    @staticmethod
    def _content_type(path: tuple[str, ...], body: str) -> str:
        lines = [line.strip() for line in body.split("\n") if line.strip()]
        if path and path[-1].endswith("?"):
            return "faq"
        listed = sum(
            1 for line in lines if line.startswith(LIST_MARKERS) or line[:1].isdigit()
        )
        return "list" if lines and listed / len(lines) >= LIST_SHARE else "prose"

    @staticmethod
    def _page_title(source_metadata: dict, lifted: str) -> str:
        title = str(source_metadata.get("page_title") or "").strip()
        if lifted:
            return lifted
        if title:
            return title
        name = str(source_metadata.get("source_name") or "")
        return PurePosixPath(name).stem.replace("-", " ").replace("_", " ").strip()

    def chunk_sync(
        self, text: str, source_metadata: dict | None = None
    ) -> list[ChunkResult]:
        meta = dict(source_metadata or {})
        root = _parse(text)
        page_title = self._page_title(meta, _lift_page_title(root))
        pieces = self._emit(root, ())
        results: list[ChunkResult] = []
        for piece in pieces:
            body = piece.body.strip()
            if not body:
                continue
            crumb = BREADCRUMB_SEPARATOR.join(
                part for part in (page_title, *piece.path) if part
            )
            heading = piece.path[-1] if piece.path else page_title
            anchor = slugify_anchor(heading) if piece.path else None
            results.append(
                ChunkResult(
                    content=f"{crumb}\n\n{body}" if crumb else body,
                    chunk_index=len(results),  # dense: empty pieces are skipped
                    metadata={
                        **meta,
                        "strategy": self.name,
                        "page_title": page_title,
                        "section_heading": heading,
                        "section_path": list(piece.path),
                        "content_type": self._content_type(piece.path, body),
                        **({"anchor": anchor} if anchor else {}),
                    },
                )
            )
        return results
