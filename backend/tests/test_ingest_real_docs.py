"""Unit tests for scripts/ingest_real_docs.py page discovery and ownership.

No database: the ownership rule is checked on the compiled SQL predicate and
on the Python re-check that guards every deletion.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql

from scripts import ingest_real_docs as ingest


def _touch(directory: Path, *names: str) -> None:
    for name in names:
        (directory / name).write_text("---\nroute: /x\n---\nbody\n", encoding="utf-8")


def _sql(statement: object) -> str:
    return str(
        statement.compile(  # type: ignore[attr-defined]
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


# --- discovery -----------------------------------------------------------


def test_discovery_excludes_readme_and_global(tmp_path: Path) -> None:
    _touch(tmp_path, "home.md", "README.md", "_global.md", "readme.MD", "b.md")
    assert ingest.discover_pages(tmp_path) == ["b.md", "home.md"]


def test_discovery_picks_up_new_file(tmp_path: Path) -> None:
    _touch(tmp_path, "home.md")
    assert ingest.discover_pages(tmp_path) == ["home.md"]
    _touch(tmp_path, "new-page.md")
    assert ingest.discover_pages(tmp_path) == ["home.md", "new-page.md"]


def test_discovery_skips_non_markdown_and_directories(tmp_path: Path) -> None:
    _touch(tmp_path, "home.md", "notes.txt", "page.docx")
    (tmp_path / "real_docs").mkdir()
    (tmp_path / "folder.md").mkdir()
    assert ingest.discover_pages(tmp_path) == ["home.md"]


def test_real_data_dir_has_the_site_pages() -> None:
    pages = ingest.discover_pages()
    assert pages == sorted(pages)
    assert "home.md" in pages
    assert not {"README.md", "_global.md"} & set(pages)
    assert pages == ingest.DOCUMENTS


# --- ownership -----------------------------------------------------------


@pytest.mark.parametrize(
    ("metadata", "owned"),
    [
        (
            {"corpus": "deploy_iq_website", "ingestion_source": "site_page_snapshot"},
            True,
        ),
        (
            {
                "corpus": "deploy_iq_website",
                "ingestion_source": "site_page_snapshot",
                "route": "/",
            },
            True,
        ),
        ({}, False),
        (None, False),
        ({"corpus": "deploy_iq_website"}, False),
        ({"ingestion_source": "site_page_snapshot"}, False),
        ({"corpus": "deploy_iq_website", "ingestion_source": "admin_upload"}, False),
        ({"corpus": "other", "ingestion_source": "site_page_snapshot"}, False),
        (
            {
                "corpus": "deploy_iq_authoritative",
                "ingestion_source": "real_source_document",
            },
            False,
        ),
    ],
)
def test_is_owned(metadata: dict | None, owned: bool) -> None:
    assert ingest.is_owned(metadata) is owned


def test_ingest_writes_the_ownership_metadata() -> None:
    assert ingest.OWNED_METADATA == {
        "corpus": "deploy_iq_website",
        "ingestion_source": "site_page_snapshot",
    }


def test_owned_predicate_requires_both_markers() -> None:
    sql = _sql(ingest.owned_by_script())
    assert (
        "CAST((documents.doc_metadata ->> 'corpus') AS VARCHAR) = 'deploy_iq_website'"
        in sql
    )
    assert (
        "CAST((documents.doc_metadata ->> 'ingestion_source') AS VARCHAR) = 'site_page_snapshot'"
        in sql
    )
    assert " AND " in sql


def test_stale_query_limits_deletion_to_owned_missing_pages() -> None:
    sql = _sql(ingest.stale_owned_query(["Home.md", "about-us.md"]))
    where = sql.split("WHERE", 1)[1]
    assert (
        "(documents.doc_metadata ->> 'corpus') AS VARCHAR) = 'deploy_iq_website'"
        in where
    )
    assert (
        "(documents.doc_metadata ->> 'ingestion_source') AS VARCHAR) = 'site_page_snapshot'"
        in where
    )
    assert "lower(documents.source_name) NOT IN ('home.md', 'about-us.md')" in where


class _FakeSession:
    """Returns ``rows`` for the stale query and records what is deleted."""

    def __init__(self, rows: list[object]) -> None:
        self.rows = rows
        self.statements: list[str] = []
        self.deleted: list[object] = []
        self.committed = False

    async def scalars(self, statement: object) -> _FakeSession:
        self.statements.append(_sql(statement))
        return self

    def all(self) -> list[object]:
        return self.rows

    async def execute(self, statement: object) -> None:
        self.statements.append(_sql(statement))

    async def delete(self, doc: object) -> None:
        self.deleted.append(doc)

    def add(self, _obj: object) -> None:
        """record_audit_event adds an AuditLog row."""

    async def flush(self) -> None:
        """record_audit_event may flush."""

    async def commit(self) -> None:
        self.committed = True


class _Doc:  # pylint: disable=too-few-public-methods  # plain row stand-in
    def __init__(self, name: str, metadata: dict) -> None:
        self.id = name
        self.source_name = name
        self.doc_metadata = metadata


@pytest.mark.asyncio
async def test_remove_never_deletes_unowned_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audits: list[dict] = []

    async def fake_audit(_db: object, **kwargs: object) -> None:
        audits.append(kwargs)

    monkeypatch.setattr(ingest, "record_audit_event", fake_audit)
    owned = _Doc("gone.md", dict(ingest.OWNED_METADATA))
    admin = _Doc("admin-upload.md", {"corpus": "deploy_iq_website"})
    db = _FakeSession([owned, admin])

    removed = await ingest.remove_deleted_documents(db, ["home.md"])

    assert removed == ["gone.md"]
    assert db.deleted == [owned]
    assert db.committed
    assert [a["detail"]["source_name"] for a in audits] == ["gone.md"]
    assert audits[0]["action"] == "document_delete"


def test_metadata_updates_restores_ownership_labels() -> None:
    legacy = {
        "corpus": "deploy_iq_authoritative",
        "ingestion_source": "real_source_document",
        "route": "/about-us",
        "ingest_count": 4,
    }
    assert ingest.metadata_updates(legacy, {"route": "/about-us"}) == dict(
        ingest.OWNED_METADATA
    )


def test_metadata_updates_is_empty_when_in_sync() -> None:
    meta = {**ingest.OWNED_METADATA, "route": "/", "title": "Home"}
    assert ingest.metadata_updates(meta, {"route": "/", "title": "Home"}) == {}
    assert ingest.metadata_updates(None, {}) == dict(ingest.OWNED_METADATA)


class _MetaSession:
    """Returns ``doc`` for the page lookup; records commits."""

    def __init__(self, doc: _Doc) -> None:
        self.doc = doc
        self.commits = 0

    async def scalar(self, _statement: object) -> _Doc:
        return self.doc

    async def commit(self) -> None:
        self.commits += 1


@pytest.mark.asyncio
async def test_store_page_meta_relabels_unchanged_page_and_audits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audits: list[dict] = []

    async def fake_audit(_db: object, **kwargs: object) -> None:
        audits.append(kwargs)

    monkeypatch.setattr(ingest, "record_audit_event", fake_audit)
    doc = _Doc(
        "about-us.md",
        {"corpus": "deploy_iq_authoritative", "route": "/about-us", "ingest_count": 4},
    )
    db = _MetaSession(doc)

    await ingest._store_page_meta(db, "about-us.md", {"route": "/about-us"}, "cli:t")  # pylint: disable=protected-access

    assert doc.doc_metadata == {
        **ingest.OWNED_METADATA,
        "route": "/about-us",
        "ingest_count": 4,
    }
    assert db.commits == 1
    assert len(audits) == 1
    assert audits[0]["action"] == "document_relabel"
    assert audits[0]["detail"]["before"] == {
        "corpus": "deploy_iq_authoritative",
        "ingestion_source": None,
    }

    await ingest._store_page_meta(db, "about-us.md", {"route": "/about-us"}, "cli:t")  # pylint: disable=protected-access
    assert db.commits == 1  # already in sync: no write, no audit
    assert len(audits) == 1


@pytest.mark.asyncio
async def test_remove_with_nothing_stale_commits_nothing() -> None:
    db = _FakeSession([])
    assert await ingest.remove_deleted_documents(db, ["home.md"]) == []
    assert not db.deleted
    assert not db.committed


def test_no_page_files_stops_before_any_removal(tmp_path) -> None:
    import pytest  # noqa: PLC0415

    from scripts.ingest_real_docs import check_pages, discover_pages  # noqa: PLC0415

    assert discover_pages(tmp_path) == []
    with pytest.raises(SystemExit, match="nothing ingested or removed"):
        check_pages(discover_pages(tmp_path), tmp_path)
    check_pages(["about-us.md"], tmp_path)  # one page is enough
