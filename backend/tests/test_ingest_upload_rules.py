"""Admin uploads through the API (Postgres; disposable database only).

Hash idempotency for .md and .docx (real converter, real normalize + hash;
only embeddings are faked), in-place update with old chunks removed, one
document per page link (409 / replace), front-matter ``route`` links,
``/ingest/preview`` and cross-page de-duplication of the upload.
"""

# pytest fixtures are injected by name; the policy cache is reset through
# its private state between tests.
# pylint: disable=redefined-outer-name,unused-argument,protected-access

from __future__ import annotations

import io
import uuid
import zipfile
from collections.abc import Iterator

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.ingestion.docx_converter import convert_docx
from app.ingestion.normalizer import doc_content_hash, normalize_markdown
from app.knowledge import settings_service
from app.models.db_models import AuditLog, Chunk, Document
from tests.pg_helpers import require_disposable_database

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
SHARED = (
    "Our consultants review every Microsoft 365 control with your team in a workshop."
)


@pytest.fixture(autouse=True)
def _disposable_db_and_policy() -> Iterator[None]:
    require_disposable_database()
    settings_service._state.value = None
    settings_service._state.loaded_at = 0.0
    yield
    settings_service._state.value = None
    settings_service._state.loaded_at = 0.0


def _name(ext: str = "md") -> str:
    return f"b2up-{uuid.uuid4().hex[:10]}.{ext}"


def _para(text: str, *, bold: bool = False) -> str:
    rpr = "<w:rPr><w:b/></w:rPr>" if bold else ""
    return f"<w:p><w:r>{rpr}<w:t>{text}</w:t></w:r></w:p>"


def _docx(
    paragraphs: list[str], *, saved: str = "2026-01-01T00:00:00Z", year: int = 2026
) -> bytes:
    """A minimal real .docx; ``saved`` / ``year`` vary like a Word re-save."""
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<w:document xmlns:w="{_W}"><w:body>{"".join(paragraphs)}</w:body></w:document>'
    )
    core = (
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/'
        'metadata/core-properties" xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{saved}</dcterms:modified>'
        "</cp:coreProperties>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in (
            (
                "[Content_Types].xml",
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="xml" ContentType="application/xml"/></Types>',
            ),
            ("word/document.xml", xml),
            ("docProps/core.xml", core),
        ):
            zf.writestr(zipfile.ZipInfo(name, date_time=(year, 1, 2, 3, 4, 5)), data)
    return buf.getvalue()


DOCX_V1 = [
    _para("Baseline Assessment", bold=True),
    _para("We evaluate your tenant across five areas that matter most."),
    _para("Pricing", bold=True),
    _para("A fixed fee covers the whole assessment and the report."),
]
DOCX_V2 = [
    _para("Baseline Assessment", bold=True),
    _para("We evaluate your tenant across five areas that matter most."),
    _para("Pricing", bold=True),
    _para("Pricing is now quoted per tenant after a short discovery call."),
]


def _file(name: str, body: bytes) -> dict:
    kind = "text/markdown" if name.endswith(".md") else "application/octet-stream"
    return {"file": (name, body, kind)}


async def _chunks(db: AsyncSession, doc_id: str) -> list[str]:
    rows = await db.scalars(
        select(Chunk.content).where(Chunk.document_id == uuid.UUID(doc_id))
    )
    return list(rows.all())


async def _doc_count(db: AsyncSession) -> int:
    return await db.scalar(select(func.count(Document.id))) or 0


# --- hash idempotency -----------------------------------------------------------


async def test_md_reupload_is_unchanged_and_change_updates_in_place(
    client: AsyncClient, db_session: AsyncSession, fake_embed: dict
) -> None:
    name = _name()
    v1 = b"# Policy\n\n## Leave\n\nLeave is twenty days.\n\n## Travel\n\nBook early."
    first = (await client.post("/ingest/file", files=_file(name, v1))).json()
    assert first["status"] == "new"
    embeds = len(fake_embed["calls"])

    again = (await client.post("/ingest/file", files=_file(name, v1))).json()
    assert again["status"] == "unchanged"
    assert again["document_id"] == first["document_id"]
    assert again["content_hash"] == first["content_hash"]
    assert (again["chunks_added"], again["chunks_removed"]) == (0, 0)
    assert len(fake_embed["calls"]) == embeds  # nothing re-embedded

    v2 = v1.replace(b"Leave is twenty days.", b"Leave is twenty-five days.")
    changed = (await client.post("/ingest/file", files=_file(name, v2))).json()
    assert changed["status"] == "updated"
    assert changed["document_id"] == first["document_id"]
    assert changed["chunks_removed"] > 0
    contents = await _chunks(db_session, first["document_id"])
    assert any("twenty-five" in c for c in contents)
    assert not any("twenty days" in c for c in contents)  # no old copy left
    assert (
        await db_session.scalar(
            select(func.count(Document.id)).where(Document.source_name == name)
        )
        == 1
    )


async def test_docx_conversion_is_deterministic_across_resaves() -> None:
    a = await convert_docx(_docx(DOCX_V1))
    b = await convert_docx(_docx(DOCX_V1, saved="2026-06-30T10:00:00Z", year=2025))
    assert a.markdown == b.markdown
    assert doc_content_hash(normalize_markdown(a.markdown)[0]) == doc_content_hash(
        normalize_markdown(b.markdown)[0]
    )


async def test_docx_reupload_is_unchanged_and_change_updates_in_place(
    client: AsyncClient, db_session: AsyncSession, fake_embed: dict
) -> None:
    name = _name("docx")
    first = (
        await client.post("/ingest/file", files=_file(name, _docx(DOCX_V1)))
    ).json()
    assert first["status"] == "new", first
    assert first["converted_from"] == "docx"
    embeds = len(fake_embed["calls"])

    # Same content, re-saved (different zip timestamps and core properties).
    resaved = _docx(DOCX_V1, saved="2026-07-01T09:00:00Z", year=2025)
    again = (await client.post("/ingest/file", files=_file(name, resaved))).json()
    assert again["status"] == "unchanged"
    assert again["document_id"] == first["document_id"]
    assert again["chunks_added"] == 0
    assert len(fake_embed["calls"]) == embeds

    changed = (
        await client.post("/ingest/file", files=_file(name, _docx(DOCX_V2)))
    ).json()
    assert changed["status"] == "updated"
    assert changed["document_id"] == first["document_id"]
    assert changed["chunks_removed"] > 0
    contents = await _chunks(db_session, first["document_id"])
    assert any("quoted per tenant" in c for c in contents)
    assert not any("fixed fee" in c for c in contents)


async def test_docx_and_md_of_same_name_are_one_document(
    client: AsyncClient, db_session: AsyncSession, fake_embed: dict
) -> None:
    stem = _name("x")[:-2]
    md = (
        await client.post(
            "/ingest/file", files=_file(f"{stem}.md", b"# A\n\nText one.")
        )
    ).json()
    docx = (
        await client.post("/ingest/file", files=_file(f"{stem}.docx", _docx(DOCX_V1)))
    ).json()
    assert docx["document_id"] == md["document_id"]
    assert docx["source_name"] == f"{stem}.md"


# --- one document per page link -------------------------------------------------


async def test_same_link_under_other_name_is_409_then_replace(
    client: AsyncClient, db_session: AsyncSession, fake_embed: dict
) -> None:
    old_name, new_name = _name(), _name("docx")
    url = f"https://docs.example.com/{uuid.uuid4().hex[:8]}"
    old = (
        await client.post(
            "/ingest/file",
            files=_file(old_name, b"# Old\n\nThe old copy of the page."),
            data={"source_url": url},
        )
    ).json()
    before = await _doc_count(db_session)

    conflict = await client.post(
        "/ingest/file",
        files=_file(new_name, _docx(DOCX_V1)),
        data={"source_url": url + "/"},  # same page
    )
    assert conflict.status_code == 409, conflict.text
    body = conflict.json()
    assert body["code"] == "document_link_in_use"
    assert body["existing_document"] == {
        "id": old["document_id"],
        "name": old_name,
        "link": url,
    }
    assert await _doc_count(db_session) == before

    other = await _doc_count(db_session)
    mismatch = await client.post(
        "/ingest/file",
        files=_file(new_name, _docx(DOCX_V1)),
        data={"source_url": url, "replace_document_id": str(uuid.uuid4())},
    )
    assert mismatch.status_code == 422
    assert mismatch.json()["code"] == "replace_document_mismatch"
    assert await _doc_count(db_session) == other

    replaced = await client.post(
        "/ingest/file",
        files=_file(new_name, _docx(DOCX_V1)),
        data={"source_url": url, "replace_document_id": old["document_id"]},
    )
    assert replaced.status_code == 200, replaced.text
    payload = replaced.json()
    assert payload["status"] == "new"
    assert payload["replaced_document"]["id"] == old["document_id"]
    assert payload["replaced_chunks_removed"] > 0
    assert await db_session.get(Document, uuid.UUID(old["document_id"])) is None
    assert await _chunks(db_session, old["document_id"]) == []
    assert await _doc_count(db_session) == before
    audit = await db_session.scalar(
        select(AuditLog)
        .where(AuditLog.action == "document_delete")
        .order_by(AuditLog.id.desc())
    )
    assert audit.detail["document_id"] == old["document_id"]
    assert audit.detail["reason"] == "replaced_by_upload"
    assert audit.detail["replaced_by_document_id"] == payload["document_id"]


# --- route links ------------------------------------------------------------------


async def test_front_matter_route_links_to_site_page(
    client: AsyncClient, db_session: AsyncSession, fake_embed: dict
) -> None:
    route = f"/b2-{uuid.uuid4().hex[:8]}"
    body = f"---\nroute: {route}\ntitle: Page\n---\n# Page\n\nPage text.".encode()
    res = (await client.post("/ingest/file", files=_file(_name(), body))).json()
    expected = get_settings().site_base_url.rstrip("/") + route
    assert res["source_url"] == expected
    assert res["link_source"] == "front_matter_route"
    doc = await db_session.get(Document, uuid.UUID(res["document_id"]))
    assert doc.doc_metadata["route"] == route


# --- preview ----------------------------------------------------------------------


async def test_preview_reports_status_link_and_changes_nothing(
    client: AsyncClient, db_session: AsyncSession, fake_embed: dict
) -> None:
    name = _name()
    url = f"https://docs.example.com/{uuid.uuid4().hex[:8]}"
    body = b"# Guide\n\n## One\n\nFirst part.\n\n## Two\n\nSecond part."
    before = await _doc_count(db_session)
    new = await client.post(
        "/ingest/preview", files=_file(name, body), data={"source_url": url}
    )
    assert new.status_code == 200, new.text
    p = new.json()
    assert (p["content_status"], p["canonical_name"]) == ("new", name)
    assert (p["detected_link"], p["link_source"]) == (url, "form")
    assert p["existing_document"] is None
    assert p["chunks_estimate"] >= 1
    assert await _doc_count(db_session) == before
    assert fake_embed["calls"] == []

    saved = (
        await client.post(
            "/ingest/file", files=_file(name, body), data={"source_url": url}
        )
    ).json()
    same = (await client.post("/ingest/preview", files=_file(name, body))).json()
    assert same["content_status"] == "unchanged"
    assert same["content_hash"] == saved["content_hash"]
    assert (same["detected_link"], same["link_source"]) == (url, "stored")
    assert same["existing_document"]["id"] == saved["document_id"]
    assert same["existing_document"]["matched_by"] == "name"

    edited = (
        await client.post("/ingest/preview", files=_file(name, body + b"\n\nMore."))
    ).json()
    assert edited["content_status"] == "changed"

    other = (
        await client.post(
            "/ingest/preview", files=_file(_name(), body), data={"source_url": url}
        )
    ).json()
    assert other["existing_document"]["matched_by"] == "link"
    assert other["link_in_use_by"]["id"] == saved["document_id"]
    assert other["replace_required"] is True
    assert any(w.startswith("document_link_in_use") for w in other["warnings"])


# --- cross-page de-duplication ----------------------------------------------------


async def test_upload_drops_lines_a_more_specific_page_has(
    client: AsyncClient, db_session: AsyncSession, fake_embed: dict
) -> None:
    tag = uuid.uuid4().hex[:8]
    specific = f"---\nroute: /b2-{tag}/zero-trust\n---\n# ZT\n\n{SHARED}\n".encode()
    owner = (await client.post("/ingest/file", files=_file(_name(), specific))).json()
    owner_query = select(Document.canonical_markdown).where(
        Document.id == uuid.UUID(owner["document_id"])
    )
    owner_text = await db_session.scalar(owner_query)

    general = f"---\nroute: /b2-{tag}\n---\n# Hub\n\n{SHARED}\n\nOwn hub copy.".encode()
    res = (await client.post("/ingest/file", files=_file(_name(), general))).json()
    assert res["duplicates_removed"] == [
        {"text": SHARED.lower()[:120], "kept_on": owner["source_name"]}
    ]
    assert any(w.startswith("duplicate_removed") for w in res["warnings"])
    contents = " ".join(await _chunks(db_session, res["document_id"]))
    assert "Own hub copy." in contents
    assert SHARED not in contents
    assert await db_session.scalar(owner_query) == owner_text  # never mutated

    again = (
        await client.post("/ingest/file", files=_file(res["source_name"], general))
    ).json()
    assert again["status"] == "unchanged"  # dedupe is deterministic
