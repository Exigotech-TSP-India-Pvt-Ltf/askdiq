"""Imports every Azure Insights source so its ``azure.<name>`` job handler and
its snapshot kinds are registered (imported by ``app.jobs.handlers`` and the
API routes). Add new source modules here."""

# pylint: disable=unused-import
from __future__ import annotations

from app.azure_insights import (
    advisor,  # noqa: F401 - azure.advisor (Phase 4)
    capacity,  # noqa: F401 - azure.capacity (Phase 4)
    health,  # noqa: F401 - azure.health (Phase 4)
    metrics,  # noqa: F401 - azure.metrics (Phase 2)
    reconciliation,  # noqa: F401 - azure.reconciliation snapshots (Phase 2)
    retail,  # noqa: F401 - azure.retail (Phase 1)
    service,  # noqa: F401 - azure.status
)
