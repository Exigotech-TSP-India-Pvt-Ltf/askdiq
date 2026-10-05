"""Structure-aware chunking for Markdown and structured text."""

import re
from collections import Counter

from app.chunking.base import ChunkingStrategy, ChunkResult


# Markdown ATX headings only: # .. ######
# Standalone bold lines (numbered items, UI labels, CTAs) are NOT headings —
# they are noise in this corpus and must never create a section boundary.
HEADER_RE = re.compile(
    r"^(#{1,6})\s+(.*)$",
    re.MULTILINE,
)

# A heading's visible text fully wrapped in bold, e.g. "## **Platform Overview**".
BOLD_WRAP_RE = re.compile(r"^\*\*(.+)\*\*$")

CODE_FENCE_RE = re.compile(
    r"```.*?```",
    re.DOTALL,
)


def _clean_heading_text(raw: str) -> str:
    """Strip bold markers so `section_heading` metadata is plain text."""
    text = raw.strip()
    match = BOLD_WRAP_RE.match(text)
    if match:
        text = match.group(1).strip()
    return text.replace("**", "").strip()


class StructureAwareChunker(ChunkingStrategy):
    name = "structure_aware"

    def __init__(self, max_chunk_chars: int = 2000):
        self.max_chunk_chars = max_chunk_chars

    def chunk(
        self,
        text: str,
        source_metadata: dict | None = None,
    ) -> list[ChunkResult]:

        placeholders: dict[str, str] = {}

        def _stash(match: re.Match) -> str:
            key = f"__CODEBLOCK_{len(placeholders)}__"
            placeholders[key] = match.group(0)
            return key

        # Protect fenced code blocks before detecting headings.
        protected_text = CODE_FENCE_RE.sub(
            _stash,
            text,
        )

        sections = self._split_by_structure(protected_text)

        results: list[ChunkResult] = []
        idx = 0

        for heading, body in sections:
            # Restore protected code blocks.
            for key, block in placeholders.items():
                body = body.replace(key, block)

            pieces = self._split_to_max_len(body)

            for piece in pieces:
                if not piece.strip():
                    continue

                content = (
                    f"Section: {heading}\n\n{piece.strip()}"
                    if heading
                    else piece.strip()
                )

                results.append(
                    ChunkResult(
                        content=content,
                        chunk_index=idx,
                        metadata={
                            **(source_metadata or {}),
                            "strategy": self.name,
                            "section_heading": heading,
                        },
                    )
                )

                idx += 1

        return results

    @staticmethod
    def _split_by_structure(
        text: str,
    ) -> list[tuple[str, str]]:
        """
        Detect structural sections using ONLY real Markdown ATX headings
        (#..######). Standalone bold lines never create a boundary.

        Nesting rule: a heading closes the currently open section only when
        its level is the SAME OR SHALLOWER (e.g. H2 closes H2, H1 closes H2).
        A deeper heading (e.g. H3 under H2) folds into the open section along
        with its own body, so numbered items / bullet "cards" grouped under
        one real heading stay together in one chunk.

        A heading level that appears only once in the document (typically a
        one-off page title `# ...`) is treated as non-sectioning "front
        matter": it can't anchor a section on its own (that would make the
        entire rest of the document nest under it), so its text is folded
        forward into whichever heading opens the next real section. This is
        generic — it depends on repetition counts, never on heading wording.
        """

        raw_matches = [
            (
                match.start(),
                match.end(),
                len(match.group(1)),
                _clean_heading_text(match.group(2)),
            )
            for match in HEADER_RE.finditer(text)
        ]

        if not raw_matches:
            return [("", text)]

        level_counts = Counter(level for _, _, level, _ in raw_matches)
        has_recurring_level = any(count > 1 for count in level_counts.values())

        # (level, heading_text, body) tuples, in document order. level=None
        # marks the preamble before the first heading (has no heading text).
        entries: list[tuple[int | None, str, str]] = []

        if raw_matches[0][0] > 0:
            entries.append((None, "", text[: raw_matches[0][0]]))

        for index, (_, end, level, heading_text) in enumerate(raw_matches):
            next_start = (
                raw_matches[index + 1][0] if index + 1 < len(raw_matches) else len(text)
            )
            entries.append((level, heading_text, text[end:next_start]))

        sections: list[tuple[str, str]] = []
        current: dict | None = None
        pending: list[
            tuple[str, str]
        ] = []  # deferred (heading_text, body) front matter

        def _flush_pending() -> str:
            prefix = ""
            for label, body in pending:
                prefix += (f"{label}\n\n" if label else "") + body
            pending.clear()
            return prefix

        for level, heading_text, body in entries:
            if level is None:
                if body.strip():
                    pending.append(("", body))
                continue

            if current is not None and level <= current["level"]:
                sections.append((current["heading"], "".join(current["parts"])))
                current = None

            is_recurring = level_counts[level] > 1

            if current is None:
                if is_recurring or not has_recurring_level:
                    current = {
                        "level": level,
                        "heading": heading_text,
                        "parts": [_flush_pending(), body],
                    }
                else:
                    # One-off heading (e.g. lone page title) — defer instead
                    # of anchoring, so it doesn't swallow every later section.
                    pending.append((heading_text, body))
            else:
                # Deeper heading nested under the open section: fold in.
                current["parts"].append(
                    f"{heading_text}\n\n{body}" if heading_text else body
                )

        if current is not None:
            current["parts"].append(_flush_pending())
            sections.append((current["heading"], "".join(current["parts"])))
        elif pending:
            heading = pending[0][0]
            sections.append((heading, _flush_pending()))

        return sections

    def _split_to_max_len(
        self,
        body: str,
    ) -> list[str]:
        """
        Split oversized sections at paragraph boundaries.

        We do not cut blindly at character 2000.
        """

        if len(body) <= self.max_chunk_chars:
            return [body]

        paragraphs = body.split("\n\n")

        pieces: list[str] = []
        current = ""

        for paragraph in paragraphs:
            paragraph = paragraph.strip()

            if not paragraph:
                continue

            candidate = f"{current}\n\n{paragraph}" if current else paragraph

            if current and len(candidate) > self.max_chunk_chars:
                pieces.append(current)
                current = paragraph
            else:
                current = candidate

        if current:
            pieces.append(current)

        return pieces
