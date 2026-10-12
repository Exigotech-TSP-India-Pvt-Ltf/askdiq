"use client";

import { useEffect, useMemo, useState } from "react";
import { CircleDollarSign, Hash, ListChecks, Users as UsersIcon } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

type GroupRow = {
  key: string;
  sessions: number;
  queries: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost_usd: number;
};

type SessionRow = {
  session_id: string;
  session_title: string;
  user_email: string;
  ip_address: string;
  queries: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost_usd: number;
  last_activity_at: string | null;
};

type QueryRow = {
  session_id: string;
  session_title: string;
  user_email: string;
  ip_address: string;
  query: string;
  request_id: string | null;
  created_at: string | null;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost_usd: number;
  is_actual?: boolean;
};

type Report = {
  by_account: GroupRow[];
  by_ip: GroupRow[];
  sessions: SessionRow[];
  queries: QueryRow[];
  token_accounting?: { actual: number; estimated: number };
};

const num = (n: number) => n.toLocaleString();
const money = (n: number) => `$${n.toFixed(4)}`;
const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "—");

function Stat({ icon: Icon, label, value }: { icon: React.ElementType; label: string; value: string }) {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-ink/10 bg-white px-4 py-3.5">
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-brand-50 text-brand-600">
        <Icon className="h-4 w-4" />
      </div>
      <div className="min-w-0">
        <p className="text-[11px] uppercase tracking-wide text-ink/40">{label}</p>
        <p className="text-lg font-semibold text-ink">{value}</p>
      </div>
    </div>
  );
}

function Table({ head, rows }: { head: string[]; rows: (string | number)[][] }) {
  if (!rows.length) {
    return <p className="px-1 py-4 text-sm text-ink/40">No data yet.</p>;
  }
  return (
    <div className="overflow-x-auto rounded-lg border border-ink/10">
      <table className="w-full text-left text-xs">
        <thead className="bg-ink/5 text-ink/60">
          <tr>
            {head.map((h) => (
              <th key={h} className="px-3 py-2 font-medium">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="border-t border-ink/5 odd:bg-white even:bg-ink/[0.02] hover:bg-brand-50/40">
              {row.map((cell, j) => (
                <td key={j} className="px-3 py-2 text-ink/80">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const TABS = ["By tester (account)", "By user (IP)"] as const;
type Tab = (typeof TABS)[number];

export default function UsageCostPage() {
  const { token } = useAuth();
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<Tab>("By tester (account)");

  useEffect(() => {
    if (!token) return;
    api
      .adminGet<Report>(token, "/usage/cost-breakdown")
      .then(setReport)
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : "Couldn't load usage & cost data.")
      );
  }, [token]);

  const byAccount = report?.by_account ?? [];
  const byIp = report?.by_ip ?? [];

  const totals = useMemo(() => {
    const groups = activeTab === "By tester (account)" ? byAccount : byIp;
    return groups.reduce(
      (acc, g) => ({
        input: acc.input + g.input_tokens,
        output: acc.output + g.output_tokens,
        total: acc.total + g.total_tokens,
        cost: acc.cost + g.cost_usd,
        queries: acc.queries + g.queries,
      }),
      { input: 0, output: 0, total: 0, cost: 0, queries: 0 }
    );
  }, [activeTab, byAccount, byIp]);

  const isAccountTab = activeTab === "By tester (account)";
  const groups = isAccountTab ? byAccount : byIp;
  const groupLabel = isAccountTab ? "Account (tester)" : "IP address (user)";

  const sessionsSorted = [...(report?.sessions ?? [])].sort((a, b) =>
    isAccountTab
      ? a.user_email.localeCompare(b.user_email)
      : a.ip_address.localeCompare(b.ip_address)
  );

  const queriesSorted = [...(report?.queries ?? [])].sort((a, b) =>
    isAccountTab
      ? a.user_email.localeCompare(b.user_email)
      : a.ip_address.localeCompare(b.ip_address)
  );

  return (
    <div className="h-full overflow-y-auto">
      <header className="flex h-16 shrink-0 items-center border-b border-ink/10 bg-white px-6">
        <div>
          <h1 className="text-sm font-semibold text-ink">Usage &amp; Cost</h1>
          <p className="text-xs text-ink/50">
            Token count and cost, per query and per session — tracked separately for tester
            accounts and for end users (by IP).
          </p>
        </div>
      </header>

      <div className="px-6 py-6">
        {error && <p className="text-sm text-red-600">{error}</p>}
        {!report && !error && <p className="text-sm text-ink/50">Loading…</p>}

        {report && (
          <>
            <div className="mb-6 flex flex-wrap gap-1 rounded-xl border border-ink/10 bg-white p-1.5">
              {TABS.map((t) => (
                <button
                  key={t}
                  onClick={() => setActiveTab(t)}
                  className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
                    activeTab === t
                      ? "bg-brand-600 text-white shadow-sm"
                      : "text-ink/55 hover:bg-ink/5 hover:text-ink"
                  }`}
                >
                  {t}
                </button>
              ))}
            </div>

            <div className="mb-6 grid grid-cols-2 gap-4 sm:grid-cols-4">
              <Stat icon={ListChecks} label="Queries" value={num(totals.queries)} />
              <Stat icon={Hash} label="Input tokens" value={num(totals.input)} />
              <Stat icon={Hash} label="Output tokens" value={num(totals.output)} />
              <Stat icon={CircleDollarSign} label="Total cost" value={money(totals.cost)} />
            </div>

            {report.token_accounting && (
              <p className="mb-6 text-xs text-ink/50">
                {report.token_accounting.actual} of{" "}
                {report.token_accounting.actual + report.token_accounting.estimated} queries use{" "}
                <span className="font-medium text-ink/70">actual</span> token counts reported by
                Azure OpenAI.
                {report.token_accounting.estimated > 0 &&
                  ` The remaining ${report.token_accounting.estimated} (recorded before actual-usage tracking was added, or with no real LLM call) use a text-length estimate instead.`}
              </p>
            )}

            <section className="mb-6 rounded-xl border border-ink/10 bg-white p-5">
              <h2 className="mb-4 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-ink/50">
                <span className="h-3 w-1 rounded-full bg-brand-500" />
                <UsersIcon className="h-3.5 w-3.5" />
                {isAccountTab ? "Tokens & cost by account" : "Tokens & cost by IP address"}
              </h2>
              <Table
                head={[groupLabel, "Sessions", "Queries", "Input tokens", "Output tokens", "Total tokens", "Cost (USD)"]}
                rows={groups.map((g) => [
                  g.key,
                  g.sessions,
                  g.queries,
                  num(g.input_tokens),
                  num(g.output_tokens),
                  num(g.total_tokens),
                  money(g.cost_usd),
                ])}
              />
            </section>

            <section className="mb-6 rounded-xl border border-ink/10 bg-white p-5">
              <h2 className="mb-4 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-ink/50">
                <span className="h-3 w-1 rounded-full bg-brand-500" />
                Session-wise tokens &amp; cost
              </h2>
              <Table
                head={[
                  "Session",
                  groupLabel,
                  "Queries",
                  "Input tokens",
                  "Output tokens",
                  "Total tokens",
                  "Cost (USD)",
                  "Last activity",
                ]}
                rows={sessionsSorted.map((s) => [
                  s.session_title || s.session_id.slice(0, 8),
                  isAccountTab ? s.user_email : s.ip_address,
                  s.queries,
                  num(s.input_tokens),
                  num(s.output_tokens),
                  num(s.total_tokens),
                  money(s.cost_usd),
                  when(s.last_activity_at),
                ])}
              />
            </section>

            <section className="mb-6 rounded-xl border border-ink/10 bg-white p-5">
              <h2 className="mb-4 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-ink/50">
                <span className="h-3 w-1 rounded-full bg-brand-500" />
                Query-wise tokens &amp; cost
              </h2>
              <Table
                head={[
                  "Query",
                  groupLabel,
                  "Session",
                  "Input tokens",
                  "Output tokens",
                  "Total tokens",
                  "Cost (USD)",
                  "Source",
                  "When",
                ]}
                rows={queriesSorted.map((q) => [
                  q.query || "—",
                  isAccountTab ? q.user_email : q.ip_address,
                  q.session_title || q.session_id.slice(0, 8),
                  num(q.input_tokens),
                  num(q.output_tokens),
                  num(q.total_tokens),
                  money(q.cost_usd),
                  q.is_actual ? "Actual" : "Estimated",
                  when(q.created_at),
                ])}
              />
            </section>
          </>
        )}
      </div>
    </div>
  );
}
