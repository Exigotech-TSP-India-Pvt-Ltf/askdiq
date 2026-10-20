"""Read schemas of the Phase 4 Azure Insights API: capacity / quota, health
(Resource Health + Service Health) and Advisor. Served from snapshots."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

RegionalState = Literal["ok", "forbidden", "error", "never_run"]
BannerLevel = Literal["warning", "critical"]


class DeploymentCapacity(BaseModel):
    name: str
    model_name: str | None = None
    model_version: str | None = None
    sku_name: str | None = Field(default=None, description="e.g. GlobalStandard.")
    sku_capacity: int | None = None
    current_capacity: int | None = None
    tpm_limit: int | None = Field(
        default=None,
        description="Tokens per minute from `rateLimits` (key `token`), "
        "normalised by `renewalPeriod`. Null when Azure reports none.",
    )
    rpm_limit: int | None = Field(
        default=None, description="Requests per minute (key `request`)."
    )
    provisioning_state: str | None = None
    role: Literal["chat", "embedding"] | None = Field(
        default=None, description="The AskDIQ deployment this is, if any."
    )


class RegionalQuotaItem(BaseModel):
    name: str = Field(description="Quota name, e.g. OpenAI.GlobalStandard.gpt-5-mini.")
    label: str
    current_value: float | None = None
    limit_value: float | None = None
    unit: str | None = None
    pct: float | None = Field(default=None, description="current / limit, in %.")


class RegionalQuota(BaseModel):
    state: RegionalState = Field(
        description="`forbidden`: the optional Cognitive Services Usages Reader "
        "role (subscription scope) is not granted."
    )
    location: str | None = None
    error_code: str | None = Field(default=None, description="Sanitised.")
    fetched_at: datetime | None = None
    items: list[RegionalQuotaItem] = []


class CapacityChange(BaseModel):
    name: str
    fetched_at: datetime
    sku_name: str | None = None
    sku_capacity: int | None = None
    tpm_limit: int | None = None
    rpm_limit: int | None = None
    prev_sku_name: str | None = None
    prev_sku_capacity: int | None = None
    prev_tpm_limit: int | None = None
    prev_rpm_limit: int | None = None


class AzureCapacityRead(BaseModel):
    """Deployments (latest poll), regional quota and recent changes."""

    connected: bool
    fetched_at: datetime | None = Field(
        default=None, description="When the deployments were last polled."
    )
    deployments: list[DeploymentCapacity]
    regional: RegionalQuota
    changes: list[CapacityChange] = []
    generated_at: datetime


class ResourceHealth(BaseModel):
    availability_state: str = Field(
        description="Available | Degraded | Unavailable | Unknown."
    )
    summary: str | None = None
    reason_type: str | None = None
    occurred_at: datetime | None = None
    reported_at: datetime | None = None
    resolution_eta: datetime | None = None
    since: datetime = Field(description="When this status was first observed.")
    checked_at: datetime
    ended_at: datetime | None = None
    stale: bool = Field(
        default=False, description="Not confirmed by a recent poll (ignored)."
    )


class ServiceEvent(BaseModel):
    tracking_id: str
    event_type: str | None = None
    status: str | None = None
    level: str | None = None
    title: str | None = None
    summary: str | None = None
    services: list[str] = []
    regions: list[str] = []
    impact_start: datetime | None = None
    impact_mitigation: datetime | None = None
    last_update: datetime | None = None
    last_seen_at: datetime
    active: bool = Field(
        description="Status Active and still reported by the latest polls."
    )


class HealthBannerState(BaseModel):
    """What the console-wide banner shows (hidden when `show` is false)."""

    show: bool
    level: BannerLevel | None = None
    title: str | None = None
    message: str | None = None
    href: str = "/admin/governance?tab=Monitoring#azure-health"
    tracking_id: str | None = None


class AzureHealthRead(BaseModel):
    connected: bool
    service_health_enabled: bool
    banner: HealthBannerState
    resource: ResourceHealth | None = None
    history: list[ResourceHealth] = Field(
        default=[], description="Recent status changes (newest first)."
    )
    events: list[ServiceEvent] = Field(
        default=[], description="Recent in-scope Service Health events."
    )
    generated_at: datetime


class AdvisorRecommendation(BaseModel):
    rec_id: str
    category: str | None = None
    impact: str | None = None
    impacted_field: str | None = None
    impacted_value: str | None = None
    problem: str | None = None
    solution: str | None = None
    learn_more_link: str | None = None
    retirement: bool = Field(description="A model / version retirement notice.")
    retirement_date: str | None = None
    last_updated: datetime | None = None
    first_seen_at: datetime


class AzureAdvisorRead(BaseModel):
    connected: bool
    enabled: bool = Field(description="AZURE_ADVISOR_ENABLED.")
    recommendations: list[AdvisorRecommendation]
    generated_at: datetime
