"""Schemas for the alerts API (``/admin/alerts``)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SeverityName = Literal["info", "warning", "critical"]
StatusName = Literal["open", "acknowledged", "resolved"]
NotifyStatusName = Literal[
    "pending",
    "queued",
    "sent",
    "retrying",
    "failed",
    "disabled",
    "skipped",
    "suppressed",
]


class AlertRead(BaseModel):
    """One alert. ``details`` carries value/threshold/unit/window/counts and
    request or job ids (PII-masked); ``console_path`` deep-links the page."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    rule: str
    rule_name: str
    severity: SeverityName
    status: StatusName
    title: str
    summary: str
    component: str
    details: dict[str, Any]
    occurrences: int
    first_seen_at: datetime
    last_seen_at: datetime
    acknowledged_at: datetime | None
    acknowledged_by: str | None
    resolved_at: datetime | None
    resolved_by: str | None
    notify_status: NotifyStatusName = Field(
        description=(
            "Email delivery: disabled (SMTP not configured), skipped (below "
            "ALERT_EMAIL_MIN_SEVERITY), suppressed (cooldown), queued, retrying, "
            "sent or failed."
        )
    )
    notify_attempts: int
    notify_error: str | None
    notified_at: datetime | None
    console_path: str
    runbook: str


class AlertPage(BaseModel):
    items: list[AlertRead]
    total: int
    limit: int
    offset: int
    active_by_severity: dict[str, int] = Field(
        description="Open + acknowledged alerts per severity (all, not just this page)."
    )


class AlertRuleRead(BaseModel):
    id: str
    name: str
    component: str
    source: Literal["database", "process"]
    trigger: str
    severity: str
    console_path: str
    runbook: str


class AlertCatalogueRead(BaseModel):
    rules: list[AlertRuleRead]
    email_enabled: bool = Field(
        description="True when SMTP_HOST, ALERT_EMAIL_FROM and ALERT_EMAIL_TO are set."
    )
    email_min_severity: SeverityName
    cooldown_minutes: int
    evaluate_interval_seconds: int
