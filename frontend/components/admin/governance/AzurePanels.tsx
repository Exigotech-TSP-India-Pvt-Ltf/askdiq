"use client";

/**
 * Governance → Monitoring: Azure capacity and health (Azure Insights Phase 4).
 *
 * - Azure health: Resource Health of the account, recent status changes and
 *   Service Health events for our service and region.
 * - Regional quota: `currentValue / limit` per model quota, plus each
 *   deployment's Azure-reported TPM / RPM (the gauges' real denominators).
 * - Advisor (optional): model / version retirements first.
 *
 * Every card reads a precomputed snapshot; the `azure.*` live scope refetches
 * them after each background poll. A page view never calls Azure.
 */
import React from "react";
import { Activity, Cloud, Gauge, Lightbulb } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import {
  REGIONAL_STATE_COPY,
  azureApi,
  azureKeys,
  compactLimit,
  type AdvisorRecommendation,
  type AzureAdvisor,
  type AzureCapacity,
  type AzureHealth,
  type RegionalQuota,
  type ServiceEvent,
} from "@/lib/azure-capacity-api";
import { useApiQuery } from "@/lib/query-cache";
import { StudioCard, StudioPill, StudioSectionHeader } from "@/components/ui/StudioCard";
import { RelativeTime } from "@/components/ui/RelativeTime";
import type { StudioTone } from "@/components/ui/styles";

const NOT_CONNECTED = "Not connected: Azure Insights is off (see foundry-usage-data-review.md, “How to enable”).";
/** Regional quota bar colours (allocation, %). */
const QUOTA_WARN_PCT = 70;
const QUOTA_DANGER_PCT = 85;
const EVENTS_SHOWN = 5;
const HISTORY_SHOWN = 5;

const STATE_TONE: Record<string, StudioTone> = {
  Available: "ok",
  Degraded: "warn",
  Unavailable: "danger",
};

export function availabilityTone(state: string | null | undefined): StudioTone {
  return (state && STATE_TONE[state]) || "info";
}

function Muted({ children }: { children: React.ReactNode }) {
  return <p className="text-xs leading-5 text-fg-muted">{children}</p>;
}

function EventRow({ event }: { event: ServiceEvent }) {
  return (
    <li className="rounded-lg border border-line px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <StudioPill tone={event.active ? (event.level === "Error" ? "danger" : "warn") : "info"}>
          {event.active ? "Active" : event.status ?? "Resolved"}
        </StudioPill>
        <span className="text-[11px] text-fg-subtle">{event.event_type}</span>
        <span className="text-[11px] tabular-nums text-fg-subtle">{event.tracking_id}</span>
      </div>
      <p className="mt-1 text-xs font-medium text-fg">{event.title ?? event.tracking_id}</p>
      <p className="text-[11px] text-fg-muted">
        {(event.regions ?? []).join(", ")} · updated <RelativeTime value={event.last_update ?? null} />
      </p>
    </li>
  );
}

export function AzureHealthCard({ health }: { health: AzureHealth | undefined }) {
  const resource = health?.resource;
  const events = (health?.events ?? []).slice(0, EVENTS_SHOWN);
  const history = (health?.history ?? []).slice(0, HISTORY_SHOWN);
  const state = resource && !resource.stale ? resource.availability_state : null;
  return (
    <StudioCard
      title="Azure health"
      subtitle="Resource Health every 10 min · Service Health every 30 min"
      icon={Activity}
      tone={availabilityTone(state)}
      pill={state ?? (health?.connected ? "No status yet" : "Not connected")}
      data-testid="azure-health-card"
    >
      {!health?.connected ? (
        <Muted>{NOT_CONNECTED}</Muted>
      ) : (
        <div className="space-y-4">
          {resource ? (
            <div>
              <p className="text-sm text-fg">{resource.summary ?? resource.availability_state}</p>
              <p className="mt-0.5 text-[11px] text-fg-subtle">
                Since <RelativeTime value={resource.since} /> · checked <RelativeTime value={resource.checked_at} />
                {resource.stale ? " · not confirmed recently" : ""}
              </p>
            </div>
          ) : (
            <Muted>No Resource Health status yet; it appears after the next poll.</Muted>
          )}
          <div>
            <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-fg-subtle">Service Health</p>
            {!health.service_health_enabled ? (
              <Muted>Off (AZURE_SERVICE_HEALTH_ENABLED needs subscription-scope read).</Muted>
            ) : events.length === 0 ? (
              <Muted>No incidents for Azure OpenAI / Foundry in our region in the last 30 days.</Muted>
            ) : (
              <ul className="space-y-2" aria-label="Service Health events">
                {events.map((e) => (
                  <EventRow key={e.tracking_id} event={e} />
                ))}
              </ul>
            )}
          </div>
          {history.length > 1 ? (
            <div>
              <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-fg-subtle">Status changes</p>
              <ul className="space-y-1 text-xs" aria-label="Status changes">
                {history.map((h) => (
                  <li key={h.since} className="flex items-center justify-between gap-2">
                    <StudioPill tone={availabilityTone(h.availability_state)}>{h.availability_state}</StudioPill>
                    <RelativeTime value={h.since} className="text-[11px] text-fg-subtle" />
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      )}
    </StudioCard>
  );
}

function barTone(pct: number): string {
  if (pct >= QUOTA_DANGER_PCT) return "bg-status-danger";
  if (pct >= QUOTA_WARN_PCT) return "bg-status-warn";
  return "bg-brand-500";
}

export function RegionalQuotaList({ regional }: { regional: RegionalQuota | undefined }) {
  const state = regional?.state ?? "never_run";
  if (state !== "ok") return <Muted>{REGIONAL_STATE_COPY[state] || REGIONAL_STATE_COPY.never_run}</Muted>;
  const items = regional?.items ?? [];
  if (items.length === 0) return <Muted>No quota in use in {regional?.location ?? "this region"}.</Muted>;
  return (
    <ul className="space-y-3" aria-label="Regional quota">
      {items.map((item) => {
        const pct = Math.max(0, Math.min(100, item.pct ?? 0));
        return (
          <li key={item.name}>
            <div className="flex items-baseline justify-between gap-2 text-xs">
              <span className="min-w-0 truncate text-fg" title={item.name}>
                {item.label}
              </span>
              <span className="shrink-0 tabular-nums text-fg-muted">
                {compactLimit(item.current_value)} / {compactLimit(item.limit_value)}
                {item.pct != null ? ` · ${Math.round(item.pct)}%` : ""}
              </span>
            </div>
            <div
              className="mt-1 h-1.5 overflow-hidden rounded-full bg-surface-muted"
              role="meter"
              aria-label={`${item.label} allocated`}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(pct)}
            >
              <div className={`h-full rounded-full ${barTone(pct)}`} style={{ width: `${pct}%` }} />
            </div>
          </li>
        );
      })}
    </ul>
  );
}

export function RegionalQuotaCard({ capacity }: { capacity: AzureCapacity | undefined }) {
  const deployments = capacity?.deployments ?? [];
  return (
    <StudioCard
      title="Regional quota and limits"
      subtitle={`Azure quota${capacity?.regional?.location ? ` in ${capacity.regional.location}` : ""} · polled every 6 h`}
      icon={Gauge}
      tone={capacity?.regional?.state === "forbidden" ? "warn" : "brand"}
      data-testid="azure-quota-card"
    >
      {!capacity?.connected ? (
        <Muted>{NOT_CONNECTED}</Muted>
      ) : (
        <div className="space-y-4">
          <RegionalQuotaList regional={capacity.regional} />
          {deployments.length > 0 ? (
            <div>
              <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-fg-subtle">
                Deployment limits (from Azure)
              </p>
              <table className="w-full text-xs">
                <thead className="text-left text-[11px] text-fg-subtle">
                  <tr>
                    <th className="px-2 py-1 font-medium">Deployment</th>
                    <th className="px-2 py-1 text-right font-medium">TPM</th>
                    <th className="px-2 py-1 text-right font-medium">RPM</th>
                  </tr>
                </thead>
                <tbody>
                  {deployments.map((d) => (
                    <tr key={d.name} className="border-t border-line">
                      <td className="px-2 py-1.5">
                        <span className="text-fg">{d.name}</span>
                        <span className="block text-[11px] text-fg-subtle">
                          {[d.role, d.sku_name, d.model_version].filter(Boolean).join(" · ")}
                        </span>
                      </td>
                      <td className="px-2 py-1.5 text-right tabular-nums">{compactLimit(d.tpm_limit)}</td>
                      <td className="px-2 py-1.5 text-right tabular-nums">{compactLimit(d.rpm_limit)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
          <p className="text-[11px] text-fg-subtle">
            Polled <RelativeTime value={capacity.fetched_at ?? null} fallback="not yet" />
          </p>
        </div>
      )}
    </StudioCard>
  );
}

function RecommendationRow({ rec }: { rec: AdvisorRecommendation }) {
  return (
    <li className="rounded-lg border border-line px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        {rec.retirement ? <StudioPill tone="warn">Retirement</StudioPill> : null}
        {rec.impact ? <span className="text-[11px] text-fg-subtle">{rec.impact} impact</span> : null}
        {rec.category ? <span className="text-[11px] text-fg-subtle">{rec.category}</span> : null}
        {rec.retirement_date ? <span className="text-[11px] tabular-nums text-fg-subtle">{rec.retirement_date}</span> : null}
      </div>
      <p className="mt-1 text-xs font-medium text-fg">{rec.problem ?? rec.rec_id}</p>
      {rec.solution ? <p className="text-[11px] text-fg-muted">{rec.solution}</p> : null}
      {rec.learn_more_link?.startsWith("https://") ? (
        <a
          href={rec.learn_more_link}
          target="_blank"
          rel="noopener noreferrer"
          className="text-[11px] font-semibold text-brand-600 underline-offset-2 hover:underline dark:text-brand-300"
        >
          Learn more
        </a>
      ) : null}
    </li>
  );
}

export function AdvisorCard({ advisor }: { advisor: AzureAdvisor | undefined }) {
  const recs = advisor?.recommendations ?? [];
  const retirements = recs.filter((r) => r.retirement).length;
  return (
    <StudioCard
      title="Advisor"
      subtitle="Recommendations for the resource group · daily"
      icon={Lightbulb}
      tone={retirements ? "warn" : "brand"}
      pill={retirements ? `${retirements} retirement${retirements === 1 ? "" : "s"}` : undefined}
      data-testid="azure-advisor-card"
    >
      {!advisor?.connected ? (
        <Muted>{NOT_CONNECTED}</Muted>
      ) : !advisor.enabled ? (
        <Muted>Off (AZURE_ADVISOR_ENABLED needs subscription-scope read).</Muted>
      ) : recs.length === 0 ? (
        <Muted>No open recommendations.</Muted>
      ) : (
        <ul className="space-y-2" aria-label="Advisor recommendations">
          {recs.map((r) => (
            <RecommendationRow key={r.rec_id} rec={r} />
          ))}
        </ul>
      )}
    </StudioCard>
  );
}

/** The Monitoring section (anchor `#azure-health`, the banner's "Details"). */
export function AzureMonitoringSection() {
  const { token } = useAuth();
  const opts = { enabled: Boolean(token), revalidateOnFocus: false };
  const capacity = useApiQuery<AzureCapacity>(azureKeys.capacity(), (s) => azureApi.capacity(token, s), opts);
  const health = useApiQuery<AzureHealth>(azureKeys.health(), (s) => azureApi.health(token, s), opts);
  const advisor = useApiQuery<AzureAdvisor>(azureKeys.advisor(), (s) => azureApi.advisor(token, s), opts);
  return (
    <section id="azure-health" aria-labelledby="gov-azure" className="scroll-mt-6 space-y-4">
      <StudioSectionHeader
        icon={Cloud}
        title={<span id="gov-azure">Azure capacity and health</span>}
        subtitle="Polled from Azure in the background (free reads) · updates live"
      />
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <AzureHealthCard health={health.data} />
        <RegionalQuotaCard capacity={capacity.data} />
        <AdvisorCard advisor={advisor.data} />
      </div>
    </section>
  );
}
