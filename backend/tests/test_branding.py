"""Branding unit tests (planchanges4 B3.2, OWASP A05). No database needed.

Covers palette derivation and the WCAG check, asset sniffing / SVG
sanitizing, URL validation and the API's pre-database rejections (413, 415,
422, 403) plus the OpenAPI contract.
"""

from __future__ import annotations

import importlib.util
import io
import uuid
import zlib
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
from PIL import Image
from pydantic import ValidationError

from app.auth.deps import authenticate_session
from app.auth.permissions import Role, UserStatus
from app.auth.principal import AuthUser
from app.branding import service
from app.branding.assets import (
    PNG_SIGNATURE,
    AssetError,
    AssetMime,
    safe_attribute_value,
    sanitize_svg,
    validate_asset,
)
from app.branding.defaults import default_brand_values
from app.branding.palette import (
    DARK_BG,
    RAMP_STEPS,
    WHITE,
    PaletteError,
    check_palette,
    contrast_ratio,
    derive_ramp,
    normalize_hex,
    palette_for,
    relative_luminance,
)
from app.core.config import Settings, get_settings
from app.core.database import get_db, get_session_factory
from app.schemas.branding import BrandingUpdate, validate_link
from main import app

BACKEND_DIR = Path(__file__).resolve().parents[1]
MAX_BYTES = 512 * 1024
LIMITS = {"max_bytes": MAX_BYTES, "max_dimension": 4096}

# ---------------------------------------------------------------------------
# Fixtures and builders
# ---------------------------------------------------------------------------


def png_bytes(width: int = 12, height: int = 8) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (0, 128, 128)).save(buf, "PNG")
    return buf.getvalue()


def webp_bytes(width: int = 12, height: int = 8, *, lossless: bool = False) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (0, 128, 128)).save(
        buf, "WEBP", lossless=lossless
    )
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _clear_branding_cache() -> Iterator[None]:
    service.invalidate_branding_cache()
    yield
    service.invalidate_branding_cache()


@pytest.fixture
async def api() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers={"X-API-Key": get_settings().api_key_name or "ci-test-key"},
    ) as client:
        yield client


class _NoDb:
    """Any use fails: proves a request was rejected before touching the DB."""

    def __getattr__(self, name: str) -> object:
        raise RuntimeError("database must not be used")


@pytest.fixture
def no_db() -> Iterator[None]:
    async def _db() -> AsyncIterator[_NoDb]:
        yield _NoDb()

    def _no_factory():
        # GET /branding opens its own sessions through the factory; make that
        # fail too, so the test never reaches a real (or stale pooled) DB.
        def _factory():
            raise ConnectionRefusedError("database unavailable (test)")

        return _factory

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_session_factory] = _no_factory
    service.invalidate_branding_cache()
    yield
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_session_factory, None)
    service.invalidate_branding_cache()


# ---------------------------------------------------------------------------
# Palette and WCAG
# ---------------------------------------------------------------------------


def test_teal_primary_gives_the_expected_ramp_and_passes() -> None:
    ramp = palette_for("#008080")
    assert list(ramp) == [str(step) for step in RAMP_STEPS]
    assert ramp["600"] == "#008080"
    assert ramp["50"] == "#e3f8f7"
    assert ramp["400"] == "#70afae"
    assert ramp["800"] == "#005050"
    assert ramp["950"] == "#002929"
    luminances = [relative_luminance(ramp[str(step)]) for step in RAMP_STEPS]
    assert luminances == sorted(luminances, reverse=True)
    assert contrast_ratio(ramp["600"], WHITE) >= 4.5
    assert contrast_ratio(ramp["400"], DARK_BG) >= 4.5


def test_yellow_primary_is_rejected_with_a_reason() -> None:
    with pytest.raises(PaletteError) as excinfo:
        palette_for("#FFFF00")
    reason = str(excinfo.value)
    assert "brand-600 #ffff00 on white" in reason
    assert "4.5:1" in reason


def test_black_primary_fails_the_dark_theme_check() -> None:
    with pytest.raises(PaletteError, match="brand-400 .* on dark background"):
        check_palette(derive_ramp("#000000"))


def test_contrast_ratio_reference_values() -> None:
    assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast_ratio("#ffffff", "#ffffff") == pytest.approx(1.0)
    assert contrast_ratio("#008080", "#ffffff") == pytest.approx(4.77, abs=0.01)


@pytest.mark.parametrize("bad", ["008080", "#00808", "#GGGGGG", "teal", "#0080801"])
def test_invalid_hex_is_rejected(bad: str) -> None:
    with pytest.raises(ValueError, match="#RRGGBB"):
        normalize_hex(bad)


def test_ramp_is_valid_hex_for_extreme_colours() -> None:
    for primary in ("#ff0000", "#00ff00", "#0000ff", "#ffffff", "#123456"):
        ramp = derive_ramp(primary)
        assert all(normalize_hex(value) == value for value in ramp.values())


# ---------------------------------------------------------------------------
# Raster assets
# ---------------------------------------------------------------------------


def test_png_and_webp_are_sniffed_with_dimensions() -> None:
    png = validate_asset(png_bytes(30, 20), "image/png", **LIMITS)
    assert (png.mime, png.width, png.height) == (AssetMime.PNG, 30, 20)
    lossy = validate_asset(webp_bytes(33, 21), None, **LIMITS)
    assert (lossy.mime, lossy.width, lossy.height) == (AssetMime.WEBP, 33, 21)
    lossless = validate_asset(webp_bytes(7, 5, lossless=True), "", **LIMITS)
    assert (lossless.width, lossless.height) == (7, 5)


def test_extension_and_neutral_declared_type_are_ignored() -> None:
    asset = validate_asset(png_bytes(), "application/octet-stream", **LIMITS)
    assert asset.mime is AssetMime.PNG


def test_polyglot_png_with_appended_html_is_rejected() -> None:
    polyglot = png_bytes() + b"<html><script>alert(document.cookie)</script></html>"
    with pytest.raises(AssetError) as excinfo:
        validate_asset(polyglot, "image/png", **LIMITS)
    assert excinfo.value.status_code == 415


def test_html_disguised_as_png_is_rejected_on_mime() -> None:
    html = b"<!doctype html><html><body><script>alert(1)</script></body></html>"
    with pytest.raises(AssetError) as excinfo:
        validate_asset(html, "image/png", **LIMITS)
    assert excinfo.value.status_code == 415


def test_declared_type_must_match_the_sniffed_type() -> None:
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"/>'
    with pytest.raises(AssetError) as excinfo:
        validate_asset(svg, "image/png", **LIMITS)
    assert excinfo.value.status_code == 415


def test_png_with_bad_crc_is_rejected() -> None:
    data = bytearray(png_bytes())
    data[len(PNG_SIGNATURE) + 8 + 2] ^= 0xFF  # flip a byte inside IHDR
    with pytest.raises(AssetError, match="checksum"):
        validate_asset(bytes(data), None, **LIMITS)


def test_truncated_png_is_rejected() -> None:
    with pytest.raises(AssetError, match="Malformed PNG"):
        validate_asset(png_bytes()[:40], None, **LIMITS)


def test_gif_and_other_formats_are_rejected() -> None:
    with pytest.raises(AssetError) as excinfo:
        validate_asset(b"GIF89a" + b"\x00" * 32, None, **LIMITS)
    assert excinfo.value.status_code == 415


def test_oversized_dimensions_are_rejected() -> None:
    with pytest.raises(AssetError) as excinfo:
        validate_asset(png_bytes(64, 8), None, max_bytes=MAX_BYTES, max_dimension=32)
    assert excinfo.value.status_code == 422


def test_file_over_512_kb_is_413_and_empty_is_422() -> None:
    with pytest.raises(AssetError) as excinfo:
        validate_asset(b"\x00" * (600 * 1024), None, **LIMITS)
    assert excinfo.value.status_code == 413
    with pytest.raises(AssetError) as excinfo:
        validate_asset(b"", None, **LIMITS)
    assert excinfo.value.status_code == 422


def test_png_crc_helper_matches_zlib() -> None:
    # Sanity check for the fixture builder above: PIL writes valid CRCs.
    data = png_bytes()
    ihdr = data[len(PNG_SIGNATURE) + 4 : len(PNG_SIGNATURE) + 8 + 13]
    crc = int.from_bytes(data[len(PNG_SIGNATURE) + 21 : len(PNG_SIGNATURE) + 25])
    assert zlib.crc32(ihdr) & 0xFFFFFFFF == crc


# ---------------------------------------------------------------------------
# SVG sanitizing (XSS vectors)
# ---------------------------------------------------------------------------

XSS_SVG = b"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"
     width="120" height="40" viewBox="0 0 120 40" onload="alert('onload')">
  <script type="text/javascript">alert('script')</script>
  <foreignObject width="10" height="10">
    <body xmlns="http://www.w3.org/1999/xhtml"><iframe src="javascript:alert(1)"/></body>
  </foreignObject>
  <a xlink:href="javascript:alert('link')"><rect width="5" height="5"/></a>
  <defs><linearGradient id="g"><stop offset="0" stop-color="#008080"/></linearGradient></defs>
  <rect id="r" width="10" height="10" fill="url(#g)" onclick="alert('click')"
        onmouseover="alert(2)" style="fill:url(http://evil.example/x.svg#a)"/>
  <circle r="3" fill="url( 'javascript:alert(3)' )" style="background:\\75rl(x)"/>
  <use xlink:href="http://evil.example/sprite.svg#icon"/>
  <use href="#r"/>
  <image href="data:image/svg+xml;base64,PHN2Zz48L3N2Zz4="/>
  <set attributeName="onmouseover" to="alert(4)"/>
  <animate attributeName="href" values="javascript:alert(5)"/>
  <style>@import url(http://evil.example/x.css); rect { fill: red }</style>
  <text x="1" y="9">Exigo &amp; Tech</text>
</svg>"""


def test_svg_xss_vectors_are_neutralized() -> None:
    asset = validate_asset(XSS_SVG, "image/svg+xml", **LIMITS)
    assert asset.mime is AssetMime.SVG
    assert (asset.width, asset.height) == (120, 40)
    out = asset.data.decode().lower()
    for needle in (
        "script",
        "onload",
        "onclick",
        "onmouseover",
        "javascript",
        "foreignobject",
        "iframe",
        "evil.example",
        "data:",
        "<style",
        "@import",
        "<set",
        "<animate",
        "<a ",
        "\\75",
    ):
        assert needle not in out, needle
    # Safe content survives.
    assert 'fill="url(#g)"' in out
    assert '<use href="#r">' in out
    assert "exigo &amp; tech" in out
    assert out.startswith('<svg xmlns="http://www.w3.org/2000/svg"')


def test_sanitized_svg_is_stable() -> None:
    once = sanitize_svg(XSS_SVG)
    assert sanitize_svg(once) == once


@pytest.mark.parametrize(
    "payload",
    [
        # DTD / entity expansion (billion laughs, XXE).
        b'<!DOCTYPE svg [<!ENTITY a "aaaa">]><svg xmlns="http://www.w3.org/2000/svg">&a;</svg>',
        b'<!DOCTYPE svg SYSTEM "http://evil.example/x.dtd"><svg xmlns="http://www.w3.org/2000/svg"/>',
        # Not SVG at all, or SVG without its namespace.
        b"<html><svg xmlns='http://www.w3.org/2000/svg'/></html>",
        b"<svg><script>alert(1)</script></svg>",
        b"not xml at all",
        b"\xff\xfe\x00<",
    ],
)
def test_non_svg_or_dangerous_xml_is_rejected(payload: bytes) -> None:
    with pytest.raises(AssetError) as excinfo:
        validate_asset(payload, None, **LIMITS)
    assert excinfo.value.status_code == 415


def test_deeply_nested_svg_is_rejected() -> None:
    body = "<g>" * 60 + "</g>" * 60
    svg = f'<svg xmlns="http://www.w3.org/2000/svg">{body}</svg>'.encode()
    with pytest.raises(AssetError, match="nested"):
        validate_asset(svg, None, **LIMITS)


@pytest.mark.parametrize(
    ("value", "safe"),
    [
        ("#008080", True),
        ("url(#grad)", True),
        ("url('#grad')", True),
        ("url(https://evil.example/a)", False),
        ("url (https://evil.example/a)", False),
        ("JaVaScRiPt:alert(1)", False),
        ("java\tscript:alert(1)", False),
        ("&#106;avascript:alert(1)", False),
        ("expression(alert(1))", False),
        ("\\75rl(x)", False),
    ],
)
def test_attribute_value_filter(value: str, safe: bool) -> None:
    assert safe_attribute_value(value) is safe


# ---------------------------------------------------------------------------
# URL and update validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        " JavaScript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "http://example.com/privacy",
        "//evil.example/privacy",
        "/\\evil.example",
        "https://user:pass@example.com/",
        "https:///nohost",
        "privacy",
        "https://exa mple.com",
        "vbscript:msgbox(1)",
    ],
)
def test_url_validation_rejects_unsafe_links(url: str) -> None:
    with pytest.raises(ValidationError):
        BrandingUpdate(version=1, privacy_url=url)


@pytest.mark.parametrize(
    "url", ["https://exigotech.co/privacy", "/terms", "/help?x=1#y"]
)
def test_url_validation_accepts_https_and_relative(url: str) -> None:
    assert validate_link(url) == url
    assert BrandingUpdate(version=1, help_url=url).help_url == url


def test_blank_url_clears_the_field() -> None:
    update = BrandingUpdate(version=1, terms_url="  ")
    assert update.changes() == {"terms_url": None}


def test_update_rules() -> None:
    with pytest.raises(ValidationError, match="No fields to update"):
        BrandingUpdate(version=1)
    with pytest.raises(ValidationError, match="cannot be null"):
        BrandingUpdate(version=1, product_name=None)
    with pytest.raises(ValidationError):
        BrandingUpdate(version=1, unknown_field="x")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        BrandingUpdate(version=1, login_headline="line\nbreak")
    with pytest.raises(ValidationError):
        BrandingUpdate(version=1, chat_sample_questions=["q"] * 9)
    with pytest.raises(ValidationError):
        BrandingUpdate(version=1, logo_light_asset_id="../../etc/passwd")
    update = BrandingUpdate(version=3, primary_hex="#00AA00", accent_hex=None)
    assert update.changes() == {"primary_hex": "#00aa00", "accent_hex": None}


# ---------------------------------------------------------------------------
# Resolution and seed
# ---------------------------------------------------------------------------


def test_resolve_defaults_keep_legacy_keys_and_derive_palette() -> None:
    settings = get_settings()
    branding = service.resolve({}, settings)
    assert branding.brand_name == settings.brand_name
    assert branding.brand_assistant_name == settings.brand_assistant_name
    assert branding.brand_support_email == settings.brand_support_email
    assert branding.primary_hex == "#008080"
    assert branding.accent_hex == "#00584f"
    assert branding.tint_hex == "#eaf6f5"
    assert branding.palette["600"] == "#008080"
    assert branding.assets.logo_light is None
    assert branding.privacy_url is None
    assert branding.login_eyebrow == "ASKDIQ"


def test_resolve_row_overrides_and_asset_urls() -> None:
    sha = "a" * 64
    branding = service.resolve(
        {
            "product_name": "Acme",
            "primary_hex": "#4f46e5",
            "accent_hex": None,
            "logo_light_asset_id": sha,
            "login_eyebrow": None,
            "version": 7,
        },
        get_settings(),
    )
    assert branding.brand_name == "Acme"
    assert branding.product_name == "Acme"
    assert branding.palette["600"] == "#4f46e5"
    assert branding.accent_hex == branding.palette["800"]
    assert branding.assets.logo_light == f"/branding/assets/{sha}"
    assert branding.logo_light_asset_id == sha
    assert branding.login_eyebrow is None
    assert branding.version == 7


def _migration_seed() -> dict[str, object]:
    path = (
        BACKEND_DIR
        / "alembic"
        / "versions"
        / "b0c1d2e3f4a5_add_brand_settings_and_assets.py"
    )
    spec = importlib.util.spec_from_file_location("brand_migration", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return dict(module.SEED)


def test_migration_seed_matches_config_defaults() -> None:
    fields = Settings.model_fields
    brand_fields = (
        "brand_name",
        "brand_assistant_name",
        "brand_company_name",
        "brand_support_email",
    )
    config_defaults = Settings.model_construct(
        **{name: fields[name].default for name in brand_fields}
    )
    seed = _migration_seed()
    expected = default_brand_values(config_defaults)
    for key, value in expected.items():
        if key.endswith("_asset_id"):
            assert key not in seed or seed[key] is None
            continue
        assert seed[key] == value, key
    assert seed["product_name"] == "DeployIQ"
    assert seed["assistant_name"] == "AskDIQ"
    assert seed["support_email"] == fields["brand_support_email"].default
    assert seed["id"] == 1
    assert seed["version"] == 1


# ---------------------------------------------------------------------------
# API rejections that happen before the database is touched
# ---------------------------------------------------------------------------


async def test_public_branding_falls_back_to_defaults_without_db(
    api: httpx.AsyncClient, no_db: None
) -> None:
    response = await api.get("/branding", headers={"X-API-Key": ""})
    assert response.status_code == 200
    body = response.json()
    assert body["brand_name"] == "DeployIQ"
    assert body["brand_assistant_name"] == "AskDIQ"
    assert body["brand_support_email"] == get_settings().brand_support_email
    assert body["palette"]["600"] == "#008080"
    assert response.headers["cache-control"] == "public, max-age=300"
    assert response.headers["etag"].startswith('W/"')
    again = await api.get(
        "/branding", headers={"If-None-Match": response.headers["etag"]}
    )
    assert again.status_code == 304


async def test_upload_over_512_kb_gives_413(
    api: httpx.AsyncClient, no_db: None
) -> None:
    big = PNG_SIGNATURE + b"\x00" * (600 * 1024)
    response = await api.post(
        "/admin/branding/assets",
        files={"file": ("logo.png", big, "image/png")},
    )
    assert response.status_code == 413
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_upload_polyglot_gives_415(api: httpx.AsyncClient, no_db: None) -> None:
    polyglot = png_bytes() + b"<script>alert(1)</script>"
    response = await api.post(
        "/admin/branding/assets",
        files={"file": ("logo.png", polyglot, "image/png")},
    )
    assert response.status_code == 415


async def test_put_with_javascript_url_gives_422(
    api: httpx.AsyncClient, no_db: None
) -> None:
    response = await api.put(
        "/admin/branding",
        json={"version": 1, "privacy_url": "javascript:alert(document.cookie)"},
    )
    assert response.status_code == 422


@pytest.mark.parametrize("sha", ["abc", "A" * 64, "../" + "a" * 61, "g" * 64])
async def test_asset_id_must_be_a_sha256(
    api: httpx.AsyncClient, no_db: None, sha: str
) -> None:
    response = await api.get(f"/branding/assets/{sha}")
    assert response.status_code in {404, 422}
    assert response.status_code != 200


@pytest.fixture
def admin_not_superadmin() -> Iterator[None]:
    async def _admin() -> AuthUser:
        return AuthUser(
            id=uuid.uuid4(),
            email="admin@example.com",
            name="Admin",
            role=Role.ADMIN,
            status=UserStatus.ACTIVE,
            version=1,
        )

    previous = app.dependency_overrides.get(authenticate_session)
    app.dependency_overrides[authenticate_session] = _admin
    yield
    if previous is not None:
        app.dependency_overrides[authenticate_session] = previous


async def test_admin_role_cannot_change_branding(
    api: httpx.AsyncClient, no_db: None, admin_not_superadmin: None
) -> None:
    put = await api.put("/admin/branding", json={"version": 1, "product_name": "X"})
    assert put.status_code == 403
    upload = await api.post(
        "/admin/branding/assets", files={"file": ("a.png", png_bytes(), "image/png")}
    )
    assert upload.status_code == 403


async def test_admin_role_cannot_read_branding_defaults(
    api: httpx.AsyncClient, no_db: None, admin_not_superadmin: None
) -> None:
    assert (await api.get("/admin/branding/defaults")).status_code == 403


async def test_branding_defaults_are_the_seeded_row(
    api: httpx.AsyncClient, no_db: None
) -> None:
    from app.branding import defaults

    response = await api.get("/admin/branding/defaults")
    assert response.status_code == 200
    body = response.json()
    settings = get_settings()
    assert body["product_name"] == settings.brand_name
    assert body["assistant_name"] == settings.brand_assistant_name
    assert body["accent_hex"] == defaults.ACCENT_HEX
    assert body["tint_hex"] == defaults.TINT_HEX
    assert body["login_headline"] == defaults.LOGIN_HEADLINE
    assert body["chat_sample_questions"] == list(defaults.CHAT_SAMPLE_QUESTIONS)


async def test_missing_service_key_gets_401_on_admin_routes(no_db: None) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.put("/admin/branding", json={"version": 1})
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# OpenAPI contract
# ---------------------------------------------------------------------------


def test_openapi_documents_the_branding_routes() -> None:
    schema = app.openapi()
    paths = schema["paths"]
    get_branding = paths["/branding"]["get"]
    assert get_branding["summary"] == "Public branding"
    assert get_branding["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/BrandingRead")
    assert "304" in get_branding["responses"]
    asset = paths["/branding/assets/{sha}"]["get"]
    assert {"200", "304", "404", "422"} <= set(asset["responses"])
    assert "image/svg+xml" in asset["responses"]["200"]["content"]
    put = paths["/admin/branding"]["put"]
    assert {"200", "401", "403", "409", "422"} <= set(put["responses"])
    assert put["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/BrandingUpdate"
    )
    upload = paths["/admin/branding/assets"]["post"]
    assert {"201", "401", "403", "413", "415", "422"} <= set(upload["responses"])
    assert "multipart/form-data" in upload["requestBody"]["content"]
    read = schema["components"]["schemas"]["BrandingRead"]["properties"]
    for legacy in ("brand_name", "brand_assistant_name", "brand_support_email"):
        assert legacy in read
    for field in ("palette", "assets", "privacy_url", "login_headline", "version"):
        assert field in read
