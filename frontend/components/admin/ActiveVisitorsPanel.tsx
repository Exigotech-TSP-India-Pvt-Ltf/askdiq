"use client";

import { Activity, CalendarDays, CalendarRange, UserCheck, type LucideIcon } from "lucide-react";
import { StatCard } from "@/components/admin/insights/StatCard";
import { InfoTip } from "@/components/ui/InfoTip";
import {
  ACTIVE_PERIODS,
  ACTIVE_VISITORS_DEFINITION,
  ACTIVE_VISITORS_LABEL,
  comparisonLabel,
  periodWindow,
  type ActivePeriodKey,
  type ActiveVisitors,
} from "@/lib/active-visitors";

const PERIOD_ICONS: Record<ActivePeriodKey, LucideIcon> = {
  dau: UserCheck,
  wau: CalendarDays,
  mau: CalendarRange,
};

/** The definition tip used next to every "Active visitors" label. */
export function ActiveVisitorsInfo({ align }: { align?: "left" | "right" }) {
  return (
    <InfoTip label={ACTIVE_VISITORS_LABEL} align={align}>
      {ACTIVE_VISITORS_DEFINITION}
    </InfoTip>
  );
}

/**
 * Active now / DAU / WAU / MAU with the trend vs the previous equal window.
 * Always as of now, independent of the page's date range. Renders nothing
 * when the server does not report `active_visitors`.
 */
export function ActiveVisitorsPanel({
  data,
  showNow = true,
  className = "",
}: {
  data: ActiveVisitors | undefined;
  /** False when the page already shows "Active visitors now" elsewhere. */
  showNow?: boolean;
  className?: string;
}) {
  if (!data) return null;
  return (
    <section aria-labelledby="active-visitors-heading" className={`space-y-3 ${className}`} data-testid="active-visitors">
      <div className="flex flex-wrap items-center gap-1.5">
        <h2 id="active-visitors-heading" className="text-sm font-semibold text-fg">
          {ACTIVE_VISITORS_LABEL}
        </h2>
        <ActiveVisitorsInfo align="left" />
        <span className="text-[11px] text-fg-subtle">As of now, independent of the date range</span>
      </div>
      <div className={`grid grid-cols-1 gap-4 sm:grid-cols-2 ${showNow ? "xl:grid-cols-4" : "lg:grid-cols-3"}`}>
        {showNow && (
          <StatCard
            title={`${ACTIVE_VISITORS_LABEL} now`}
            value={data.active_now}
            subtitle={`Last ${data.active_now_minutes} minutes`}
            icon={Activity}
          />
        )}
        {ACTIVE_PERIODS.map(({ key, title }) => {
          const period = data[key];
          return (
            <StatCard
              key={key}
              title={title}
              value={period.value}
              subtitle={periodWindow(period.days)}
              delta={period.delta === null ? undefined : { value: period.delta, title: comparisonLabel(period.days) }}
              icon={PERIOD_ICONS[key]}
            />
          );
        })}
      </div>
    </section>
  );
}
