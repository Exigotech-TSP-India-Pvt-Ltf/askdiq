"use client";

import { LayoutDashboard } from "lucide-react";
import { DashboardHeader } from "@/components/admin/DashboardHeader";
import { ActiveVisitorsPanel } from "@/components/admin/ActiveVisitorsPanel";
import { DashboardKpis } from "@/components/admin/dashboard/DashboardKpis";
import {
  CostWidget,
  IntentsWidget,
  LatencyWidget,
  OutcomesWidget,
  PipelineWidget,
  SatisfactionWidget,
  type WidgetProps,
} from "@/components/admin/dashboard/DashboardCharts";
import { ActivityFeed } from "@/components/admin/dashboard/ActivityFeed";
import { HealthPill } from "@/components/admin/dashboard/HealthPill";
import { useDashboardParams, useDashboardSummary } from "@/lib/use-dashboard";

/**
 * Interactive dashboard (planchanges4 F3.1, newplan §1 and §15.4). The global
 * DateRangePicker and audience drive every widget; the summary, the activity
 * feed and the health pill are independent requests, each with its own
 * skeleton, so one slow source never blocks the others. Every KPI and chart
 * mark deep-links to the page that owns the number, pre-filtered.
 */
export default function AdminDashboard() {
  const { range, params } = useDashboardParams();
  const summary = useDashboardSummary(params);
  const widget: WidgetProps = {
    data: summary.data,
    loading: summary.isLoading,
    error: summary.error,
    onRetry: () => void summary.refetch(),
    range,
  };

  return (
    <div className="min-h-screen bg-surface-page text-fg">
      <DashboardHeader
        title="Dashboard"
        subtitle="Monitor users, conversations, RAG performance and governance."
        icon={LayoutDashboard}
        showDateRange
        actions={<HealthPill />}
      />

      <div className="mx-auto w-full max-w-[1600px] space-y-6 p-6 sm:p-8">
        <DashboardKpis
          data={summary.data}
          loading={summary.isLoading}
          error={summary.error}
          onRetry={widget.onRetry}
          range={range}
        />

        <ActiveVisitorsPanel data={summary.data?.active_visitors} />

        <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
          <div className="xl:col-span-2">
            <OutcomesWidget {...widget} />
          </div>
          <SatisfactionWidget {...widget} />
        </div>

        <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
          <div className="xl:col-span-2">
            <LatencyWidget {...widget} />
          </div>
          <CostWidget {...widget} />
        </div>

        <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
          <IntentsWidget {...widget} />
          <ActivityFeed />
          <PipelineWidget {...widget} />
        </div>
      </div>
    </div>
  );
}
