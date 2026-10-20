"""Imports every Azure Insights source so its ``azure.<name>`` job handler and
its snapshot kinds are registered (imported by ``app.jobs.handlers`` and the
API routes). Add new source modules here."""

from __future__ import annotations

# pylint: disable=unused-import
from app.azure_insights import (
    retail,  # noqa: F401 - azure.retail (Phase 1)
    service,  # noqa: F401 - azure.status
)
