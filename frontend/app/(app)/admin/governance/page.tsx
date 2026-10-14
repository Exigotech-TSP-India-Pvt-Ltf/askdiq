"use client";

import { useEffect, useState } from "react";
import { Activity, AlertTriangle, FileSearch, Gauge, ScrollText, ShieldCheck } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useAudience } from "@/lib/audience-context";
import { formatLatencyMs } from "@/lib/format";
import { DashboardHeader } from "@/components/admin/DashboardHeader";
import { TabGroup, GOVERNANCE_TABS } from "@/components/admin/TabGroup";
import MonitoringView from "./monitoring/MonitoringView";
import TracesView from "./traces/TracesView";
import LogsView from "./logs/LogsView";

type Tab = "Overall" | "Monitoring" | "Traces" | "Logs";

function InfoRow({
  icon: Icon,
  label,
  value,
  detail,
}: {
  icon: React.ElementType;
  label: string;
  value: string;
  detail?: string;
}) {
  return (
    <div className="flex items-center gap-4 border-b border-slate-100 py-3.5 last:border-0">
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-indigo-50 border border-indigo-100 text-brand-indigo">
        <Icon className="h-4 w-4" />
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-slate-900">{label}</p>
        {detail && <p className="text-xs text-slate-500">{detail}</p>}
      </div>
      <p className="shrink-0 text-sm font-bold text-slate-900 font-mono">{value}</p>
    </div>
  );
}

/** Minimal, read-only summary pulled from the same endpoints the Monitoring/
 * Traces/Logs tabs use — gives a quick status glance without duplicating
 * their full dashboards. */
function OverallSummary() {
  const { token } = useAuth();
  const { audience } = useAudience();
  const [monitoring, setMonitoring] = useState<any>(null);
  const [traceCount, setTraceCount] = useState<number | null>(null);
  const [traceErrors, setTraceErrors] = useState<number | null>(null);
  const [logTotal, setLogTotal] = useState<number | null>(null);

  useEffect(() => {
    if (!token) return;
    const qs = audience && audience !== "overview" ? `?audience=${audience}` : "";
    api.adminGet<any>(token, `/governance/monitoring${qs}`).then(setMonitoring).catch(() => {});
    api
      .adminGet<any[]>(token, `/governance/traces${qs}`)
      .then((traces) => {
        setTraceCount(traces.length);
        setTraceErrors(traces.filter((t) => (t.status || "ok").toLowerCase() !== "ok").length);
      })
      .catch(() => {});
    api
      .adminGet<{ total: number }>(token, `/governance/logs?limit=1${audience && audience !== "overview" ? `&audience=${audience}` : ""}`)
      .then((data) => setLogTotal(data.total))
      .catch(() => {});
  }, [token, audience]);

  return (
    <section className="rounded-xl border border-slate-200/90 bg-white p-6 shadow-sm">
      <InfoRow
        icon={Activity}
        label="Monitoring"
        detail="Infrastructure, application and RAG pipeline health"
        value={
          monitoring?.error_rate != null
            ? `${Number(monitoring.error_rate).toFixed(1)}% error rate`
            : "Loading…"
        }
      />
      <InfoRow
        icon={Gauge}
        label="Latency"
        detail="LLM response time, end to end"
        value={monitoring?.llm_latency_ms != null ? formatLatencyMs(monitoring.llm_latency_ms) : "Loading…"}
      />
      <InfoRow
        icon={FileSearch}
        label="Traces"
        detail="Request journeys recorded"
        value={traceCount != null ? `${traceCount} total` : "Loading…"}
      />
      <InfoRow
        icon={AlertTriangle}
        label="Trace errors"
        detail="Requests that failed"
        value={traceErrors != null ? String(traceErrors) : "Loading…"}
      />
      <InfoRow
        icon={ScrollText}
        label="Logs"
        detail="Application, security and RAG governance entries"
        value={logTotal != null ? `${logTotal} entries` : "Loading…"}
      />
    </section>
  );
}

export default function GovernancePage() {
  const [activeTab, setActiveTab] = useState<Tab>("Overall");

  return (
    <div className="flex flex-col min-h-screen bg-slate-50/50">
      <DashboardHeader
        title="Admin Governance"
        subtitle="System health monitoring, request tracing, and security audit log streams"
        icon={ShieldCheck}
      />

      <div className="flex-1 p-6 sm:p-8 space-y-6 max-w-[1600px] mx-auto w-full">
        <div className="flex items-center justify-between">
          <TabGroup
            tabs={GOVERNANCE_TABS}
            activeTab={activeTab}
            onChange={(t) => setActiveTab(t as Tab)}
            ariaLabel="Governance sub-tabs"
          />
        </div>

        {activeTab === "Overall" && <OverallSummary />}

        {activeTab === "Monitoring" && (
          <div>
            <MonitoringView hideHeader />
          </div>
        )}

        {activeTab === "Traces" && (
          <div>
            <TracesView hideHeader />
          </div>
        )}

        {activeTab === "Logs" && (
          <div>
            <LogsView hideHeader />
          </div>
        )}
      </div>
    </div>
  );
}
