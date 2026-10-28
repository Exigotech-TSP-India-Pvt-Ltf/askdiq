"use client";

/**
 * Who is behind the person / visitor cross-filter: the real Visitors-page
 * records (`/admin/users/{id}`) a probable person was grouped from, and a
 * way back to the visitor when the page was opened from one. An unknown or
 * stale id shows an empty state with a "clear filter" action, never an error
 * page.
 */
import { useEffect } from "react";
import Link from "next/link";
import { ArrowUpRight, UserRound, X } from "lucide-react";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { visitorDetailHref } from "@/lib/entity-routes";
import { insightsApi, type InsightsFilters, type PersonDetail } from "@/lib/insights-api";
import { useApiQuery } from "@/lib/query-cache";
import type { LabelKind } from "./AnalyticsCards";
import { dayLabel, fmtInt } from "./format";
import { errorText, personKey } from "./use-insights";
import type { AnalyticsState } from "./url-state";

const BOX = "flex flex-col gap-2 rounded-2xl border border-line bg-surface-card p-4 text-xs shadow-xs";
const LINK =
  "group inline-flex items-center gap-1 rounded-lg border border-line px-2 py-1 text-[11px] font-semibold text-brand-700 transition-colors duration-150 hover:border-brand-300 focus:outline-hidden focus-visible:ring-2 focus-visible:ring-brand-500 dark:text-brand-300 dark:hover:border-brand-700/60";
const ICON = { className: "h-3.5 w-3.5", strokeWidth: 1.75, "aria-hidden": true } as const;

export const IDENTITY_TEXT: Record<PersonDetail["identity"], string> = {
  account: "Grouped on one signed-in account (e-mail, case-insensitive). The e-mail itself is never shown here.",
  network: "No usable sign-in on these sessions: grouped on the network (client IP) they were created from.",
  session: "A single session with neither a sign-in nor a client IP.",
};

export interface PersonPanelProps {
  state: AnalyticsState;
  filters: InsightsFilters;
  update: (patch: Partial<AnalyticsState>) => void;
  learnLabel?: (kind: LabelKind, label: string) => void;
}

function ClearButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} className={LINK}>
      <X {...ICON} />
      {label}
    </button>
  );
}

function VisitorLinks({ person }: { person: PersonDetail }) {
  if (person.visitors.length === 0 && !person.account_visitor_id) {
    return <p className="text-fg-subtle">These sessions were created without a client IP, so there is no visitor record to open.</p>;
  }
  return (
    <ul className="flex flex-wrap gap-1.5" aria-label="Visitor records">
      {person.account_visitor_id && (
        <li>
          <Link href={visitorDetailHref(person.account_visitor_id)} className={LINK}>
            Console user
            <ArrowUpRight {...ICON} />
          </Link>
        </li>
      )}
      {person.visitors.map((v) => (
        <li key={v.visitor_id}>
          <Link href={visitorDetailHref(v.visitor_id)} className={LINK} title={`Last seen ${dayLabel(v.last_seen.slice(0, 10))}`}>
            {v.visitor_id.replace(/^client-/, "")} · {fmtInt(v.sessions)} session{v.sessions === 1 ? "" : "s"}
            <ArrowUpRight {...ICON} />
          </Link>
        </li>
      ))}
    </ul>
  );
}

export function PersonPanel({ state, filters, update, learnLabel }: PersonPanelProps) {
  const { token } = useAuth();
  const personId = state.person;
  const { data, error, isLoading } = useApiQuery(
    personId ? personKey(personId, filters) : null,
    (signal) => insightsApi.getPerson(token, personId, { audience: filters.audience, tz: filters.tz }, signal),
  );
  const label = data?.person ?? null;
  useEffect(() => {
    if (label) learnLabel?.("person", label);
  }, [label, learnLabel]);

  if (!personId) return null;
  const clear = () => update({ person: "" });

  if (error instanceof ApiError && error.status === 404) {
    return (
      <section role="status" aria-label="Selected person" className={BOX} data-testid="person-panel">
        <p className="font-medium text-fg">No one matches this person link.</p>
        <p className="text-fg-subtle">
          It may be stale, or from another audience. The charts below are empty for it.
        </p>
        <div>
          <ClearButton label="Clear person filter" onClick={clear} />
        </div>
      </section>
    );
  }

  return (
    <section aria-label="Selected person" aria-busy={isLoading || undefined} className={BOX} data-testid="person-panel">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="inline-flex items-center gap-1.5 text-sm font-semibold text-fg">
          <UserRound className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
          {label ? `Person ${label}` : "Selected person"}
        </h2>
        <div className="flex flex-wrap gap-1.5">
          {state.tab !== "conversations" && (
            <button type="button" className={LINK} onClick={() => update({ tab: "conversations" })}>
              View their conversations
              <ArrowUpRight {...ICON} />
            </button>
          )}
          <ClearButton label="Clear person filter" onClick={clear} />
        </div>
      </div>
      {error ? (
        <p role="alert" className="text-status-danger-fg">
          {errorText(error)}
        </p>
      ) : data ? (
        <>
          <p className="text-fg-muted">{IDENTITY_TEXT[data.identity]}</p>
          <p className="text-fg-muted">
            {fmtInt(data.conversations)} classified conversation{data.conversations === 1 ? "" : "s"} of {fmtInt(data.sessions)} session
            {data.sessions === 1 ? "" : "s"} in this audience, all time; first seen {dayLabel(data.first_seen.slice(0, 10))}, last seen{" "}
            {dayLabel(data.last_seen.slice(0, 10))}. The charts show the selected period only.
          </p>
          <VisitorLinks person={data} />
        </>
      ) : null}
    </section>
  );
}

/** Banner for the `visitor` filter: what it is and the way back to the visitor. */
export function VisitorBanner({ state, update }: Pick<PersonPanelProps, "state" | "update">) {
  if (!state.visitor) return null;
  return (
    <section aria-label="Selected visitor" className={BOX} data-testid="visitor-panel">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-fg-muted">
          Showing conversations started by visitor <span className="font-mono text-fg">{state.visitor.replace(/^client-/, "")}</span>.
        </p>
        <div className="flex flex-wrap gap-1.5">
          <Link href={visitorDetailHref(state.visitor)} className={LINK}>
            Open visitor
            <ArrowUpRight {...ICON} />
          </Link>
          <ClearButton label="Clear visitor filter" onClick={() => update({ visitor: "" })} />
        </div>
      </div>
    </section>
  );
}
