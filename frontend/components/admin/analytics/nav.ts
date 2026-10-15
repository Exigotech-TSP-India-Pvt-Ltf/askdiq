import { ChartColumnBig, type LucideIcon } from "lucide-react";

/**
 * Sidebar entry for the Analytics page (planchanges4 F6.1). PR-17 registers
 * this item in `components/Sidebar.tsx` with `enabled: false`; once both PRs
 * are merged, flip `enabled` there (this PR does not edit the Sidebar).
 */
export const ANALYTICS_NAV_ITEM: Readonly<{
  href: string;
  label: string;
  icon: LucideIcon;
  enabled: boolean;
}> = {
  href: "/admin/analytics",
  label: "Analytics",
  icon: ChartColumnBig,
  enabled: true,
};
