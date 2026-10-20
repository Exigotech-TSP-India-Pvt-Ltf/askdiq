"""Imports every Azure Insights source so its ``azure.<name>`` job handler and
its snapshot kinds are registered (imported by ``app.jobs.handlers`` and the
API routes)."""

# pylint: disable=unused-import
from __future__ import annotations

from app.azure_insights import (  # noqa: F401
    metrics,
    reconciliation,
    service,
)
