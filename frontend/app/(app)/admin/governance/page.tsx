"use client";

import { useEffect, useState } from "react";
import { Activity, AlertTriangle, FileSearch, Gauge, ScrollText } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { formatLatencyMs } from "@/lib/format";
import MonitoringView from "./monitoring/MonitoringView";
import TracesView from "./traces/TracesView";
import LogsView from "./logs/LogsView";

const TABS = ["Overall", "Monitoring", "Traces", "Logs"] as const;
type Tab = (typeof TABS)[number];

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
    <div className="flex items-center gap-4 border-b border-purple-50 py-3 last:border-0">
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-purple-50 text-purple-600">
        <Icon className="h-4 w-4" />
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-slate-900">{label}</p>
        {detail && <p className="text-xs text-slate-500">{detail}</p>}
      </div>
      <p className="shrink-0 text-sm font-semibold text-slate-900">{value}</p>
    </div>
  );
}

/** Minimal, read-only summary pulled from the same endpoints the Monitoring/
 * Traces/Logs tabs use — gives a quick status glance without duplicating
 * their full dashboards. */
function OverallSummary() {
  const { token } = useAuth();
  const [monitoring, setMonitoring] = useState<any>(null);
  const [traceCount, setTraceCount] = useState<number | null>(null);
  const [traceErrors, setTraceErrors] = useState<number | null>(null);
  const [logTotal, setLogTotal] = useState<number | null>(null);

  useEffect(() => {
    if (!token) return;
    api.adminGet<any>(token, "/governance/monitoring").then(setMonitoring).catch(() => {});
    api
      .adminGet<any[]>(token, "/governance/traces")
      .then((traces) => {
        setTraceCount(traces.length);
        setTraceErrors(traces.filter((t) => (t.status || "ok").toLowerCase() !== "ok").length);
      })
      .catch(() => {});
    api
      .adminGet<{ total: number }>(token, "/governance/logs?limit=1")
      .then((data) => setLogTotal(data.total))
      .catch(() => {});
  }, [token]);

  return (
    <section className="rounded-2xl border border-purple-100 bg-white p-6 shadow-sm">
      <InfoRow
        icon={Activity}
        label="Monitoring"
        detail="Infrastructure, application and RAG pipeline health"
        value={
          monitoring
            ? `${(monitoring.error_rate * 100).toFixed(1)}% error rate`
            : "Loading…"
        }
      />
      <InfoRow
        icon={Gauge}
        label="Latency"
        detail="LLM response time, end to end"
        value={monitoring ? formatLatencyMs(monitoring.llm_latency_ms) : "Loading…"}
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
    <div>
      <header className="flex h-16 shrink-0 items-center border-b border-slate-200 bg-white px-8">
        <div>
          <h1 className="text-sm font-semibold text-slate-900">Governance</h1>
          <p className="text-xs text-slate-500">
            Monitor system health, request traces and governance activity.
          </p>
        </div>
      </header>

      <div className="min-h-[calc(100%-4rem)] bg-gradient-to-b from-[#f3f0ff] to-[#f7f6ff] p-8">
        <div className="mb-6 flex flex-wrap gap-1 rounded-xl border border-purple-100 bg-white p-1.5 shadow-sm">
          {TABS.map((t) => (
            <button
              key={t}
              onClick={() => setActiveTab(t)}
              className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
                activeTab === t
                  ? "bg-purple-600 text-white shadow-sm"
                  : "text-slate-500 hover:bg-slate-100 hover:text-slate-800"
              }`}
            >
              {t}
            </button>
          ))}
        </div>

        {activeTab === "Overall" && <OverallSummary />}

        {activeTab === "Monitoring" && (
          <div className="-m-8 mt-0">
            <MonitoringView hideHeader />
          </div>
        )}

        {activeTab === "Traces" && (
          <div className="-m-8 mt-0">
            <TracesView hideHeader />
          </div>
        )}

        {activeTab === "Logs" && (
          <div className="-m-8 mt-0">
            <LogsView hideHeader />
          </div>
        )}
      </div>
    </div>
  );
}
