"""OR-7 knowledge source links through the API (Postgres, runs in CI).

PATCH /documents/{id} (superadmin only, audited, no re-embed), GET/PUT
/knowledge/settings, the required-URL upload rule, file link metadata,
documents list fields, the session-detail citation refresh and the
migration round trip.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import authenticate_session
from app.auth.permissions import Role, UserStatus
from app.auth.principal import AuthUser
from app.knowledge import settings_service
from app.models.db_models import (
    AuditLog,
    ChatMessageRecord,
    ChatSession,
    Chunk,
    Document,
    KnowledgeSettings,
)
from main import app
from tests.pg_helpers import new_engine, require_postgres

BACKEND_DIR = Path(__file__).resolve().parents[1]
REVISION = "05b32ff49272"


@pytest.fixture(autouse=True)
def _reset_policy_cache() -> Iterator[None]:
    settings_service._state.value = None
    settings_service._state.loaded_at = 0.0
    yield
    settings_service._state.value = None
    settings_service._state.loaded_at = 0.0


@pytest.fixture
def as_admin() -> Iterator[None]:
    async def _admin() -> AuthUser:
        return AuthUser(
            id=uuid.uuid4(),
            email="admin@example.com",
            name="Admin",
            role=Role.ADMIN,
            status=UserStatus.ACTIVE,
            version=1,
        )

    app.dependency_overrides[authenticate_session] = _admin
    yield


def _name() -> str:
    return f"kblink-{uuid.uuid4().hex[:10]}"


async def _document(db: AsyncSession, **values: object) -> Document:
    doc = Document(
        source_name=f"{_name()}.md",
        source_type="markdown",
        doc_metadata={},
        **values,
    )
    db.add(doc)
    await db.commit()
    return doc


async def _audit(db: AsyncSession, action: str) -> list[AuditLog]:
    rows = await db.scalars(
        select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id.desc())
    )
    return list(rows.all())


async def _require_urls(db: AsyncSession, hosts: list[str] | None = None) -> None:
    db.add(
        KnowledgeSettings(
            id=1, allowed_hosts=hosts or [], require_source_url=True, version=1
        )
    )
    await db.commit()


# --- PATCH /documents/{id} -----------------------------------------------------


async def test_patch_link_sets_audits_and_never_reembeds(
    client: AsyncClient, db_session: AsyncSession, fake_embed: dict
) -> None:
    doc = await _document(db_session)
    before_updated = doc.updated_at
    res = await client.patch(
        f"/documents/{doc.id}",
        json={
            "source_url": "https://Docs.DeployIQ.com.au/guide",
            "section_links": True,
        },
    )
    assert res.status_code == 200, res.text
    assert res.json() == {
        "document_id": str(doc.id),
        "source_url": "https://docs.deployiq.com.au/guide",
        "section_links": True,
        "has_link": True,
        "link_status": "ok",
        "link_host": "docs.deployiq.com.au",
    }
    await db_session.refresh(doc)
    assert doc.source_url == "https://docs.deployiq.com.au/guide"
    assert doc.section_links is True
    assert doc.updated_at == before_updated
    assert fake_embed["calls"] == []
    (event,) = (await _audit(db_session, "document_link_updated"))[:1]
    assert event.detail["document_id"] == str(doc.id)
    assert event.detail["old_host"] is None
    assert event.detail["new_host"] == "docs.deployiq.com.au"
    assert "guide" not in str(event.detail)  # hosts only, never full URLs

    cleared = await client.patch(
        f"/documents/{doc.id}", json={"source_url": None, "section_links": False}
    )
    assert cleared.json()["link_status"] == "missing"


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("http://x.com", "invalid_source_url"),
        ("javascript:alert(1)", "invalid_source_url"),
        ("https://user:pw@x.com", "invalid_source_url"),
    ],
)
async def test_patch_link_rejects_bad_urls(
    client: AsyncClient, db_session: AsyncSession, url: str, code: str
) -> None:
    doc = await _document(db_session)
    res = await client.patch(f"/documents/{doc.id}", json={"source_url": url})
    assert res.status_code == 422
    assert res.json()["code"] == code


async def test_patch_link_enforces_allow_list_and_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    db_session.add(KnowledgeSettings(id=1, allowed_hosts=["*.deployiq.com.au"]))
    await db_session.commit()
    doc = await _document(db_session)
    res = await client.patch(
        f"/documents/{doc.id}", json={"source_url": "https://github.com/x"}
    )
    assert res.status_code == 422
    assert res.json()["code"] == "source_url_not_allowed"
    missing = await client.patch(
        f"/documents/{uuid.uuid4()}", json={"source_url": "https://a.deployiq.com.au"}
    )
    assert missing.status_code == 404


async def test_admin_cannot_edit_links_or_the_allow_list(
    client: AsyncClient, db_session: AsyncSession, as_admin: None
) -> None:
    doc = await _document(db_session)
    res = await client.patch(
        f"/documents/{doc.id}", json={"source_url": "https://a.com"}
    )
    assert res.status_code == 403
    put = await client.put(
        "/knowledge/settings",
        json={"allowed_hosts": [], "require_source_url": True, "version": 0},
    )
    assert put.status_code == 403
    # Reading the policy is open to every console user (the upload dialog).
    assert (await client.get("/knowledge/settings")).status_code == 200


# --- /knowledge/settings -------------------------------------------------------


async def test_knowledge_settings_defaults_save_audit_and_conflict(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    initial = (await client.get("/knowledge/settings")).json()
    assert initial["allowed_hosts"] == []
    assert initial["require_source_url"] is False
    assert initial["version"] == 0
    assert initial["max_hosts"] == 50

    saved = await client.put(
        "/knowledge/settings",
        json={
            "allowed_hosts": ["Docs.Example.com", "*.deployiq.com.au"],
            "require_source_url": True,
            "version": 0,
        },
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["allowed_hosts"] == ["docs.example.com", "*.deployiq.com.au"]
    assert body["require_source_url"] is True
    assert body["version"] == 1
    assert settings_service.current_knowledge_settings().require_source_url is True

    (event,) = (await _audit(db_session, "knowledge_settings_updated"))[:1]
    assert event.detail["hosts_added"] == ["docs.example.com", "*.deployiq.com.au"]
    assert event.detail["require_source_url"] == {"from": False, "to": True}

    stale = await client.put(
        "/knowledge/settings",
        json={"allowed_hosts": [], "require_source_url": False, "version": 0},
    )
    assert stale.status_code == 409
    invalid = await client.put(
        "/knowledge/settings",
        json={"allowed_hosts": ["*.com"], "require_source_url": False, "version": 1},
    )
    assert invalid.status_code == 422


# --- ingestion -----------------------------------------------------------------


def _md(name: str, body: bytes) -> dict:
    return {"file": (name, body, "text/markdown")}


async def test_required_url_rejects_new_documents_without_one(
    client: AsyncClient, db_session: AsyncSession, fake_embed: dict
) -> None:
    await _require_urls(db_session)
    name = f"{_name()}.md"
    body = b"# Policy\n\nLeave is twenty days."
    rejected = await client.post("/ingest/file", files=_md(name, body))
    assert rejected.status_code == 422
    assert rejected.json()["code"] == "source_url_required"
    assert fake_embed["calls"] == []  # rejected before any embedding

    preview = await client.post("/ingest/file?dry_run=true", files=_md(name, body))
    assert preview.status_code == 200  # a dry run never blocks on the link

    accepted = await client.post(
        "/ingest/file",
        files=_md(name, body),
        data={"source_url": "https://docs.example.com/policy"},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["source_url"] == "https://docs.example.com/policy"
    assert accepted.json()["link_status"] == "ok"

    # A re-ingest of an existing document keeps its stored link.
    again = await client.post("/ingest/file", files=_md(name, body + b"\n\nMore text."))
    assert again.status_code == 200, again.text
    assert again.json()["source_url"] == "https://docs.example.com/policy"


async def test_setting_off_allows_uploads_without_url(
    client: AsyncClient, fake_embed: dict
) -> None:
    res = await client.post("/ingest/file", files=_md(f"{_name()}.md", b"# A\n\nB c."))
    assert res.status_code == 200
    assert res.json()["source_url"] is None
    assert res.json()["link_status"] == "missing"


async def test_bad_or_disallowed_upload_url_is_422(
    client: AsyncClient, db_session: AsyncSession, fake_embed: dict
) -> None:
    db_session.add(KnowledgeSettings(id=1, allowed_hosts=["docs.example.com"]))
    await db_session.commit()
    for url, code in (
        ("http://docs.example.com", "invalid_source_url"),
        ("https://evil.com/x", "source_url_not_allowed"),
    ):
        res = await client.post(
            "/ingest/file",
            files=_md(f"{_name()}.md", b"# A\n\nB."),
            data={"source_url": url},
        )
        assert res.status_code == 422
        assert res.json()["code"] == code


async def test_front_matter_url_prefills_and_is_stripped(
    client: AsyncClient, db_session: AsyncSession, fake_embed: dict
) -> None:
    name = f"{_name()}.md"
    body = (
        b"---\nurl: https://docs.example.com/fm\ntitle: FM Guide\n---\n# Guide\n\nText."
    )
    preview = await client.post("/ingest/file?dry_run=true", files=_md(name, body))
    assert preview.json()["detected_source_url"] == "https://docs.example.com/fm"
    saved = await client.post("/ingest/file", files=_md(name, body))
    payload = saved.json()
    doc = await db_session.get(Document, uuid.UUID(payload["document_id"]))
    assert doc is not None
    assert doc.source_url == "https://docs.example.com/fm"
    assert doc.doc_metadata["title"] == "FM Guide"
    chunks = (
        await db_session.scalars(
            select(Chunk.content).where(Chunk.document_id == doc.id)
        )
    ).all()
    assert chunks
    assert all("url:" not in c and "---" not in c for c in chunks)


async def test_unchanged_reupload_with_new_url_updates_metadata_only(
    client: AsyncClient, db_session: AsyncSession, fake_embed: dict
) -> None:
    name = f"{_name()}.md"
    body = b"# Same\n\nIdentical content."
    first = await client.post(
        "/ingest/file", files=_md(name, body), data={"source_url": "https://a.com/1"}
    )
    assert first.status_code == 200
    calls = len(fake_embed["calls"])
    second = await client.post(
        "/ingest/file", files=_md(name, body), data={"source_url": "https://b.com/2"}
    )
    payload = second.json()
    assert payload["status"] == "unchanged"
    assert payload["change_reasons"] == ["source_url_changed"]
    assert payload["source_url"] == "https://b.com/2"
    assert payload["chunks_added"] == 0
    assert len(fake_embed["calls"]) == calls  # no re-embed


async def test_admin_reupload_keeps_an_existing_link(
    client: AsyncClient, db_session: AsyncSession, fake_embed: dict
) -> None:
    doc = await _document(db_session, source_url="https://old.com/x")

    async def _admin() -> AuthUser:
        return AuthUser(
            id=uuid.uuid4(),
            email="admin@example.com",
            name="Admin",
            role=Role.ADMIN,
            status=UserStatus.ACTIVE,
            version=1,
        )

    app.dependency_overrides[authenticate_session] = _admin
    res = await client.post(
        "/ingest/file",
        files=_md(doc.source_name, b"# New\n\nNew text."),
        data={"source_url": "https://new.com/y"},
    )
    assert res.status_code == 200, res.text
    payload = res.json()
    assert payload["source_url"] == "https://old.com/x"
    assert any(w.startswith("source_url_kept") for w in payload["warnings"])


async def test_documents_list_carries_link_fields(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    db_session.add(KnowledgeSettings(id=1, allowed_hosts=["docs.example.com"]))
    await db_session.commit()
    ok = await _document(db_session, source_url="https://docs.example.com/a")
    gone = await _document(db_session, source_url="https://old.example.org/a")
    missing = await _document(db_session)
    await settings_service.get_knowledge_settings(db_session)
    rows = {d["document_id"]: d for d in (await client.get("/documents")).json()}
    assert rows[str(ok.id)]["link_status"] == "ok"
    assert rows[str(ok.id)]["has_link"] is True
    assert rows[str(ok.id)]["link_host"] == "docs.example.com"
    assert rows[str(gone.id)]["link_status"] == "not_allowed"
    assert rows[str(missing.id)]["link_status"] == "missing"
    assert rows[str(missing.id)]["has_link"] is False


# --- session detail citation refresh -------------------------------------------


async def test_session_detail_refreshes_citation_links(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = f"{_name()}@example.com"
    client.headers["X-Client-Id"] = actor
    doc = await _document(
        db_session, source_url="https://docs.example.com/page", section_links=True
    )
    now = datetime.utcnow()
    session = ChatSession(
        user_email=actor,
        title="Links",
        created_at=now,
        last_activity_at=now,
        expires_at=now + timedelta(days=1),
    )
    db_session.add(session)
    await db_session.flush()
    db_session.add_all(
        [
            ChatMessageRecord(
                session_id=session.id, turn_index=0, role="user", content="Q?"
            ),
            ChatMessageRecord(
                session_id=session.id,
                turn_index=0,
                role="assistant",
                content="Answer [1].",
                token_details={
                    "citations": [
                        {
                            "n": 1,
                            "document_id": str(doc.id),
                            "title": "stale",
                            "url": "https://stale.example/x",
                            "label": "stale",
                            "host": "stale.example",
                            "anchor": "getting-started",
                        }
                    ]
                },
            ),
        ]
    )
    await db_session.commit()

    detail = (await client.get(f"/sessions/{session.id}")).json()
    cited = detail["messages"][1]["citations"]
    assert cited[0]["url"] == "https://docs.example.com/page#getting-started"
    assert cited[0]["label"] == "example"
    assert detail["messages"][0]["citations"] == []

    await client.patch(
        f"/documents/{doc.id}",
        json={"source_url": "https://docs.deployiq.com.au/new", "section_links": False},
    )
    detail = (await client.get(f"/sessions/{session.id}")).json()
    cited = detail["messages"][1]["citations"]
    assert cited[0]["url"] == "https://docs.deployiq.com.au/new"
    assert cited[0]["label"] == "deployiq"


# --- migration -----------------------------------------------------------------


def _alembic(*args: str) -> str:
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout + result.stderr


async def test_migration_upgrade_downgrade_upgrade() -> None:
    engine = new_engine()
    await require_postgres(engine)
    columns_sql = text(
        "SELECT count(*) FROM information_schema.columns WHERE table_name = "
        "'documents' AND column_name IN ('source_url', 'section_links')"
    )
    table_sql = text(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_name = 'knowledge_settings'"
    )
    try:
        await asyncio.to_thread(_alembic, "downgrade", f"{REVISION}-1")
        async with engine.connect() as conn:
            assert await conn.scalar(columns_sql) == 0
            assert await conn.scalar(table_sql) == 0
        await engine.dispose()
        await asyncio.to_thread(_alembic, "upgrade", "head")
        async with engine.connect() as conn:
            assert await conn.scalar(columns_sql) == 2  # noqa: PLR2004
            assert await conn.scalar(table_sql) == 1
    finally:
        await engine.dispose()
