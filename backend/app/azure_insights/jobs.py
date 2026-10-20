"""Imports every Azure Insights source so its ``azure.<name>`` job handler and
its snapshot kinds are registered (imported by ``app.jobs.handlers`` and the
API routes)."""

from __future__ import annotations

from app.azure_insights import service  # noqa: F401  # pylint: disable=unused-import
