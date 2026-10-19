"use client";

import { ChartColumnBig } from "lucide-react";
import { DashboardHeader } from "@/components/admin/DashboardHeader";
import { AnalyticsView } from "@/components/admin/analytics/AnalyticsView";

/** Analytics: native port of the Chatbot Insights HTML on live insights data (planchanges4 F6). */
export default function AnalyticsPage() {
  return (
    <div className="flex min-h-full flex-col">
      <DashboardHeader
        title="Analytics"
        subtitle="What people ask, how it was answered and where conversations drop"
        icon={ChartColumnBig}
        showDateRange
      />
      <AnalyticsView />
    </div>
  );
}
