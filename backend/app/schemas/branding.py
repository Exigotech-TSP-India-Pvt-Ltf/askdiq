"""Schemas for the white-label branding API (newplan section 14)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Self
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    model_validator,
)

from app.branding.assets import AssetMime
from app.branding.palette import HEX_PATTERN

ASSET_ID_PATTERN = r"^[0-9a-f]{64}$"
_SINGLE_LINE = r"^[^\x00-\x1f\x7f]*$"
_URL_MAX = 2048
MAX_SAMPLE_QUESTIONS = 8


def validate_link(value: str) -> str:
    """Allow ``https://host/...`` or a site-relative ``/path`` only.

    Anything else (``javascript:``, ``data:``, ``http:``, protocol-relative
    ``//host``, backslashes, whitespace or control characters) is rejected.
    """
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):  # noqa: PLR2004 - ASCII control range
        raise ValueError("URL must not contain whitespace or control characters")
    if "\\" in value:
        raise ValueError("URL must not contain backslashes")
    if value.startswith("/") and not value.startswith("//"):
        return value
    parts = urlsplit(value)
    if parts.scheme == "https" and parts.hostname and "@" not in parts.netloc:
        return value
    raise ValueError(
        "URL must start with https:// or be a relative path such as /privacy"
    )


def _blank_to_none(value: Any) -> Any:
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _lower(value: str) -> str:
    return value.lower()


ShortText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=80, pattern=_SINGLE_LINE
    ),
]
CompanyText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=120, pattern=_SINGLE_LINE
    ),
]
HexColor = Annotated[
    str, StringConstraints(pattern=HEX_PATTERN.pattern), AfterValidator(_lower)
]
AssetId = Annotated[str, StringConstraints(pattern=ASSET_ID_PATTERN)]
SafeUrl = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=_URL_MAX),
    AfterValidator(validate_link),
]
Question = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=200, pattern=_SINGLE_LINE
    ),
]


def _text(max_length: int, *, single_line: bool = True) -> StringConstraints:
    return StringConstraints(
        strip_whitespace=True,
        max_length=max_length,
        pattern=_SINGLE_LINE if single_line else None,
    )


_Eyebrow = Annotated[str, _text(60)]
_Headline = Annotated[str, _text(160)]
_Subtext = Annotated[str, _text(400, single_line=False)]
_Description = Annotated[str, _text(1000, single_line=False)]
LoginEyebrow = Annotated[_Eyebrow | None, BeforeValidator(_blank_to_none)]
LoginHeadline = Annotated[_Headline | None, BeforeValidator(_blank_to_none)]
LoginSubtext = Annotated[_Subtext | None, BeforeValidator(_blank_to_none)]
LoginDescription = Annotated[_Description | None, BeforeValidator(_blank_to_none)]
OptionalUrl = Annotated[SafeUrl | None, BeforeValidator(_blank_to_none)]
OptionalEmail = Annotated[EmailStr | None, BeforeValidator(_blank_to_none)]

# Columns that may not be cleared with an explicit null.
REQUIRED_FIELDS = frozenset(
    {
        "product_name",
        "assistant_name",
        "company_name",
        "support_email",
        "primary_hex",
        "chat_sample_questions",
    }
)
ASSET_FIELDS = (
    "logo_light_asset_id",
    "logo_dark_asset_id",
    "mark_light_asset_id",
    "mark_dark_asset_id",
    "favicon_asset_id",
)


class BrandingUpdate(BaseModel):
    """PUT /admin/branding body. Omitted fields keep their current value;
    ``null`` clears an optional field. ``version`` must equal the current
    version (optimistic concurrency, 409 otherwise)."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1, description="Current version from GET /branding.")
    product_name: ShortText | None = None
    assistant_name: ShortText | None = None
    company_name: CompanyText | None = None
    support_email: EmailStr | None = None
    access_contact_email: OptionalEmail = None
    primary_hex: HexColor | None = Field(
        default=None, description="Brand colour (#RRGGBB); must pass WCAG AA."
    )
    accent_hex: HexColor | None = None
    tint_hex: HexColor | None = None
    logo_light_asset_id: AssetId | None = None
    logo_dark_asset_id: AssetId | None = None
    mark_light_asset_id: AssetId | None = None
    mark_dark_asset_id: AssetId | None = None
    favicon_asset_id: AssetId | None = None
    login_eyebrow: LoginEyebrow = None
    login_headline: LoginHeadline = None
    login_subtext: LoginSubtext = None
    login_description: LoginDescription = None
    privacy_url: OptionalUrl = None
    terms_url: OptionalUrl = None
    help_url: OptionalUrl = None
    chat_sample_questions: list[Question] | None = Field(
        default=None, max_length=MAX_SAMPLE_QUESTIONS
    )

    @model_validator(mode="after")
    def _required_fields_not_cleared(self) -> Self:
        cleared = sorted(
            name
            for name in self.model_fields_set & REQUIRED_FIELDS
            if getattr(self, name) is None
        )
        if cleared:
            raise ValueError(f"These fields cannot be null: {', '.join(cleared)}")
        if not self.model_fields_set - {"version"}:
            raise ValueError("No fields to update")
        return self

    def changes(self) -> dict[str, Any]:
        return self.model_dump(include=self.model_fields_set - {"version"})


class BrandAssetUrls(BaseModel):
    logo_light: str | None = None
    logo_dark: str | None = None
    mark_light: str | None = None
    mark_dark: str | None = None
    favicon: str | None = None


class BrandingRead(BaseModel):
    """Resolved branding: DB row over config defaults, plus derived palette."""

    # Legacy keys read by the current frontend (kept for compatibility).
    brand_name: str
    brand_assistant_name: str
    brand_support_email: str

    product_name: str
    assistant_name: str
    company_name: str
    support_email: str
    access_contact_email: str | None
    primary_hex: str
    accent_hex: str
    tint_hex: str
    palette: dict[str, str] = Field(
        description='Derived "50" to "950" ramp; "600" is primary_hex.'
    )
    logo_light_asset_id: str | None = None
    logo_dark_asset_id: str | None = None
    mark_light_asset_id: str | None = None
    mark_dark_asset_id: str | None = None
    favicon_asset_id: str | None = None
    assets: BrandAssetUrls = Field(
        description="Relative asset URLs (GET /branding/assets/{sha}) or null."
    )
    login_eyebrow: str | None
    login_headline: str | None
    login_subtext: str | None
    login_description: str | None
    privacy_url: str | None
    terms_url: str | None
    help_url: str | None
    chat_sample_questions: list[str]
    version: int
    updated_at: datetime | None


class BrandAssetRead(BaseModel):
    id: str = Field(description="sha256 of the stored bytes.")
    mime: AssetMime
    size_bytes: int
    width: int | None
    height: int | None
    url: str
    created_at: datetime | None = None
