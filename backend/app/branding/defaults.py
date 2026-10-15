"""Built-in brand defaults (the Exigo Tech teal theme and DeployIQ copy).

These are the fallback when the ``brand_settings`` row is missing or the
database is unreachable, and they match the row seeded by migration
``b0c1d2e3f4a5``. Names and the support email come from settings.
"""

from __future__ import annotations

from typing import Any

from app.core.config import Settings

SINGLETON_ID = 1

PRIMARY_HEX = "#008080"
ACCENT_HEX = "#00584f"
TINT_HEX = "#eaf6f5"

LOGIN_EYEBROW = "ASKDIQ"
LOGIN_HEADLINE = "Answers you can trust, grounded in your own knowledge base."
LOGIN_SUBTEXT = (
    "AskDIQ searches approved DeployIQ documentation and cites every source, "
    "so your team can move fast without guessing."
)
LOGIN_DESCRIPTION = (
    "Sign in with your Microsoft work account to open the DeployIQ admin "
    "console and assistant."
)
CHAT_SAMPLE_QUESTIONS: tuple[str, ...] = (
    "What is the Essential Eight and how does DeployIQ implement it?",
    "How does DeployIQ keep humans in the loop over AI actions?",
    "What does the Zero Trust Professional plan include?",
    "How does DeployIQ handle customer data?",
)


def default_brand_values(settings: Settings) -> dict[str, Any]:
    """Column values of the default ``brand_settings`` row."""
    return {
        "product_name": settings.brand_name,
        "assistant_name": settings.brand_assistant_name,
        "company_name": settings.brand_company_name,
        "support_email": settings.brand_support_email,
        "access_contact_email": None,
        "primary_hex": PRIMARY_HEX,
        "accent_hex": ACCENT_HEX,
        "tint_hex": TINT_HEX,
        "logo_light_asset_id": None,
        "logo_dark_asset_id": None,
        "mark_light_asset_id": None,
        "mark_dark_asset_id": None,
        "favicon_asset_id": None,
        "login_eyebrow": LOGIN_EYEBROW,
        "login_headline": LOGIN_HEADLINE,
        "login_subtext": LOGIN_SUBTEXT,
        "login_description": LOGIN_DESCRIPTION,
        "privacy_url": None,
        "terms_url": None,
        "help_url": None,
        "chat_sample_questions": list(CHAT_SAMPLE_QUESTIONS),
    }
