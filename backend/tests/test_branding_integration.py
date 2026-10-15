"""Branding integration tests against Postgres (planchanges4 B3.1, B3.3, B3.4).

Upload a logo, update the palette, see ``GET /branding`` change with a new
ETag, and check that assets are served with safe headers. Also covers the
409 version conflict, the WCAG 422, the audit row and the migration
round-trip with its seed. Runs in CI (Postgres + pgvector).
"""

from __future__ import annotations

import asyncio
import io
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.branding import service
from app.branding.defaults import default_brand_values
from app.core.config import get_settings
from app.models.db_models import AuditLog, BrandAsset, BrandSettings
from tests.pg_helpers import new_engine, require_postgres

BACKEND_DIR = Path(__file__).resolve().parents[1]
ACTOR = "brand-admin@example.com"

SVG_WITH_SCRIPT = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" '
    b'onload="alert(1)"><script>alert(2)</script>'
    b'<rect width="64" height="64" fill="#008080"/></svg>'
)


def _png(width: int = 40, height: int = 20) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (0, 88, 79)).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _clear_branding_cache() -> Iterator[None]:
    service.invalidate_branding_cache()
    yield
    service.invalidate_branding_cache()


async def _current(client: AsyncClient) -> dict:
    response = await client.get("/branding")
    assert response.status_code == 200
    return response.json()


async def test_seed_row_matches_config_defaults(db_session: AsyncSession) -> None:
    row = await db_session.get(BrandSettings, 1)
    assert row is not None
    defaults = default_brand_values(get_settings())
    for key in (
        "primary_hex",
        "accent_hex",
        "tint_hex",
        "login_eyebrow",
        "login_headline",
    ):
        assert getattr(row, key) == defaults[key], key
    assert row.product_name == "DeployIQ"
    assert row.assistant_name == "AskDIQ"
    assert row.privacy_url is None
    assert row.terms_url is None
    assert row.help_url is None
    assert row.chat_sample_questions == defaults["chat_sample_questions"]


async def test_singleton_check_rejects_a_second_row(db_session: AsyncSession) -> None:
    with pytest.raises(IntegrityError, match="ck_brand_settings_singleton"):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "INSERT INTO brand_settings (id, product_name, assistant_name, "
                    "company_name, support_email, primary_hex) VALUES "
                    "(2, 'x', 'y', 'z', 'a@b.co', '#008080')"
                )
            )


async def test_upload_logo_update_palette_and_etag_changes(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    before = await client.get("/branding")
    assert before.status_code == 200
    assert before.headers["cache-control"] == "public, max-age=300"
    old_etag = before.headers["etag"]
    old = before.json()
    assert {"brand_name", "brand_assistant_name", "brand_support_email"} <= set(old)
    assert (
        await client.get("/branding", headers={"If-None-Match": old_etag})
    ).status_code == 304

    upload = await client.post(
        "/admin/branding/assets",
        files={"file": ("logo.png", _png(), "image/png")},
        headers={"X-Client-Id": ACTOR},
    )
    assert upload.status_code == 201, upload.text
    asset = upload.json()
    assert asset["mime"] == "image/png"
    assert (asset["width"], asset["height"]) == (40, 20)
    assert asset["url"] == f"/branding/assets/{asset['id']}"

    update = await client.put(
        "/admin/branding",
        json={
            "version": old["version"],
            "primary_hex": "#4F46E5",
            "logo_light_asset_id": asset["id"],
            "privacy_url": "https://example.com/privacy",
            "login_headline": "Welcome to Acme",
        },
        headers={"X-Client-Id": ACTOR},
    )
    assert update.status_code == 200, update.text
    updated = update.json()
    assert updated["version"] == old["version"] + 1
    assert updated["palette"]["600"] == "#4f46e5"

    after = await client.get("/branding")
    assert after.status_code == 200
    assert after.headers["etag"] != old_etag
    body = after.json()
    assert body["primary_hex"] == "#4f46e5"
    assert body["palette"]["600"] == "#4f46e5"
    assert body["assets"]["logo_light"] == asset["url"]
    assert body["privacy_url"] == "https://example.com/privacy"
    assert body["login_headline"] == "Welcome to Acme"
    assert body["brand_name"] == old["brand_name"]
    # The old ETag no longer matches; the new one does.
    assert (
        await client.get("/branding", headers={"If-None-Match": old_etag})
    ).status_code == 200
    assert (
        await client.get("/branding", headers={"If-None-Match": after.headers["etag"]})
    ).status_code == 304

    served = await client.get(asset["url"])
    assert served.status_code == 200
    assert served.content == _png()
    assert served.headers["content-type"] == "image/png"
    assert served.headers["x-content-type-options"] == "nosniff"
    assert served.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert (
        await client.get(
            asset["url"], headers={"If-None-Match": served.headers["etag"]}
        )
    ).status_code == 304

    audit = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "branding_updated", AuditLog.actor == ACTOR
            )
        )
    ).scalar_one()
    assert audit.detail["changed_fields"] == sorted(
        ["primary_hex", "logo_light_asset_id", "privacy_url", "login_headline"]
    )
    assert audit.detail["to_version"] == old["version"] + 1
    uploads = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.action == "branding_asset_uploaded")
        )
    ).scalars()
    assert any(row.detail["sha256"] == asset["id"] for row in uploads)


async def test_svg_is_sanitized_and_served_with_csp(client: AsyncClient) -> None:
    upload = await client.post(
        "/admin/branding/assets",
        files={"file": ("mark.svg", SVG_WITH_SCRIPT, "image/svg+xml")},
    )
    assert upload.status_code == 201, upload.text
    asset = upload.json()
    assert asset["mime"] == "image/svg+xml"
    served = await client.get(asset["url"])
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("image/svg+xml")
    assert (
        served.headers["content-security-policy"]
        == "default-src 'none'; style-src 'unsafe-inline'"
    )
    assert served.headers["x-content-type-options"] == "nosniff"
    assert b"script" not in served.content
    assert b"onload" not in served.content
    assert b"<rect" in served.content


async def test_upload_is_idempotent_by_content(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    first = await client.post(
        "/admin/branding/assets", files={"file": ("a.png", _png(8, 8), "image/png")}
    )
    second = await client.post(
        "/admin/branding/assets", files={"file": ("b.png", _png(8, 8), "image/png")}
    )
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    count = (
        await db_session.execute(
            select(BrandAsset).where(BrandAsset.sha256 == first.json()["id"])
        )
    ).scalars()
    assert len(list(count)) == 1


async def test_concurrent_update_gets_409(client: AsyncClient) -> None:
    version = (await _current(client))["version"]
    first = await client.put(
        "/admin/branding", json={"version": version, "product_name": "First"}
    )
    assert first.status_code == 200
    stale = await client.put(
        "/admin/branding", json={"version": version, "product_name": "Second"}
    )
    assert stale.status_code == 409
    assert stale.headers["content-type"].startswith("application/problem+json")
    assert (await _current(client))["product_name"] == "First"


async def test_parallel_updates_with_same_version_only_one_wins(
    client: AsyncClient,
) -> None:
    version = (await _current(client))["version"]
    # The shared test session serializes the statements; the version check
    # still lets exactly one of them through.
    results = []
    for name in ("A", "B"):
        results.append(
            await client.put(
                "/admin/branding", json={"version": version, "assistant_name": name}
            )
        )
    assert sorted(r.status_code for r in results) == [200, 409]


async def test_unreadable_primary_colour_is_rejected_with_reason(
    client: AsyncClient,
) -> None:
    version = (await _current(client))["version"]
    response = await client.put(
        "/admin/branding", json={"version": version, "primary_hex": "#FFFF00"}
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "WCAG" in detail
    assert "brand-600" in detail
    assert (await _current(client))["primary_hex"] == "#008080"


async def test_unknown_asset_id_is_rejected(client: AsyncClient) -> None:
    version = (await _current(client))["version"]
    response = await client.put(
        "/admin/branding", json={"version": version, "favicon_asset_id": "f" * 64}
    )
    assert response.status_code == 422
    assert "Unknown brand asset" in response.json()["detail"]


async def test_unknown_asset_is_404(client: AsyncClient) -> None:
    response = await client.get(f"/branding/assets/{'0' * 64}")
    assert response.status_code == 404


async def test_clearing_optional_links_and_accent(client: AsyncClient) -> None:
    version = (await _current(client))["version"]
    set_links = await client.put(
        "/admin/branding",
        json={"version": version, "help_url": "/help", "accent_hex": None},
    )
    assert set_links.status_code == 200
    body = set_links.json()
    assert body["help_url"] == "/help"
    assert body["accent_hex"] == body["palette"]["800"]
    cleared = await client.put(
        "/admin/branding", json={"version": body["version"], "help_url": ""}
    )
    assert cleared.status_code == 200
    assert cleared.json()["help_url"] is None


# ---------------------------------------------------------------------------
# Migration round-trip
# ---------------------------------------------------------------------------


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


async def test_branding_migration_round_trip() -> None:
    engine = new_engine()
    await require_postgres(engine)
    query = text(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_name IN ('brand_settings', 'brand_assets')"
    )
    try:
        heads = await asyncio.to_thread(_alembic, "heads")
        assert heads.count("(head)") == 1
        # Later migrations may stack on top: the branding revision must be on
        # the single linear chain to head, not necessarily head itself.
        chain = await asyncio.to_thread(_alembic, "history", "-r", "4c1e9a7d2b65:head")
        assert "b0c1d2e3f4a5" in chain

        await asyncio.to_thread(_alembic, "downgrade", "4c1e9a7d2b65")
        async with engine.connect() as conn:
            assert await conn.scalar(query) == 0
        await engine.dispose()

        await asyncio.to_thread(_alembic, "upgrade", "head")
        async with engine.connect() as conn:
            assert await conn.scalar(query) == 2
            seed = (
                await conn.execute(
                    text(
                        "SELECT product_name, assistant_name, support_email, "
                        "primary_hex, accent_hex, tint_hex, version FROM brand_settings"
                    )
                )
            ).all()
        assert seed == [
            (
                "DeployIQ",
                "AskDIQ",
                "support@deployiq.com",
                "#008080",
                "#00584f",
                "#eaf6f5",
                1,
            )
        ]
    finally:
        await engine.dispose()
