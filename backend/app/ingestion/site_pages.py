"""Website page snapshots (``backend/data/*.md``): front matter + page body.

Each file is a verbatim copy of one public page with YAML front matter
(``url``, ``route``, ``title``, ``description``; see ``backend/data/README.md``).
The body is cleaned by the shared ingestion clean-up
(``app.ingestion.cleanup``, run inside ``normalize_markdown``), so pages,
uploads and .docx conversions all get the same treatment.
"""

from __future__ import annotations

from dataclasses import dataclass

import yaml

FRONT_MATTER_FENCE = "---"


@dataclass(frozen=True, slots=True)
class SitePage:
    """One page: its site route, metadata and the Markdown body."""

    route: str | None
    url: str | None
    title: str | None
    description: str | None
    body: str


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _split_front_matter(text: str) -> tuple[dict, str]:
    lines = text.lstrip("﻿").split("\n")
    if not lines or lines[0].strip() != FRONT_MATTER_FENCE:
        return {}, text
    for index in range(1, len(lines)):
        if lines[index].strip() == FRONT_MATTER_FENCE:
            data = yaml.safe_load("\n".join(lines[1:index])) or {}
            body = "\n".join(lines[index + 1 :])
            return (data if isinstance(data, dict) else {}), body
    return {}, text


def parse_site_page(text: str) -> SitePage:
    """Front-matter fields plus the page body of one snapshot."""
    meta, body = _split_front_matter(text.replace("\r\n", "\n"))
    return SitePage(
        route=_text(meta.get("route")),
        url=_text(meta.get("url")),
        title=_text(meta.get("title")),
        description=_text(meta.get("description")),
        body=body.strip() + "\n",
    )
