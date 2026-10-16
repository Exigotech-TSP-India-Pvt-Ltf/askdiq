"use client";

/**
 * User Management (newplan §7.8, §15.4; planchanges4 F1.4): KPI strip and
 * charts, then Users, Access Requests, Roles & Permissions and Audit Trail
 * tabs. Superadmin only (`users:manage`); the API enforces the same guard.
 */
import React, { useCallback } from "react";
import { AnimatePresence, m } from "motion/react";
import { ClipboardList, KeyRound, ScrollText, ShieldAlert, UserCog, Users } from "lucide-react";
import { DashboardHeader } from "@/components/admin/DashboardHeader";
import { TabGroup, type TabItem } from "@/components/admin/TabGroup";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { SkeletonCard } from "@/components/ui/Skeleton";
import { fadeUp, useMotionVariants } from "@/lib/motion";
import { useApiQuery } from "@/lib/query-cache";
import type { DeepLinkUpdate } from "@/lib/use-deep-link";
import { AUDIT_LIMIT_MAX, umKeys, userManagementApi } from "@/lib/user-management-api";
import { AccessRequestsTab } from "./AccessRequestsTab";
import { AuthAuditTab } from "./AuthAuditTab";
import { RolesMatrixTab } from "./RolesMatrixTab";
import { UserOverview } from "./UserOverview";
import { UsersTab } from "./UsersTab";
import { useCurrentUser } from "./use-current-user";
import { useUmUrl, type UmTab } from "./use-um-url";

const STATS_DAYS = 30;
/** Filters a drill-down replaces (the rest of the URL is kept). */
const USERS_RESET: DeepLinkUpdate = { q: null, role: null, status: null, sort: null, order: null, page: null, user: null };

function errorMessage(error: unknown): string | null {
  if (!error) return null;
  return error instanceof Error && error.message ? error.message : "Failed to load.";
}

export function UserManagementPage() {
  const { me, isLoading: meLoading, error: meError, can } = useCurrentUser();
  const allowed = can("users:manage");

  if (meLoading) {
    return (
      <Shell>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
          {Array.from({ length: 5 }, (_, i) => (
            <SkeletonCard key={i} label="Loading" />
          ))}
        </div>
      </Shell>
    );
  }
  if (!allowed) {
    return (
      <Shell>
        {meError && !me ? (
          <ErrorState title="Could not check your access" message={errorMessage(meError)} />
        ) : (
          <EmptyState
            icon={ShieldAlert}
            title="Superadmins only"
            description="User Management needs the users:manage permission. Ask a superadmin if you need access."
          />
        )}
      </Shell>
    );
  }
  return <UserManagementContent meId={me?.user.id} />;
}

function Shell({ children, badge }: { children: React.ReactNode; badge?: number | null }) {
  return (
    <div className="flex min-h-full flex-col bg-surface-page text-fg">
      <div data-tour="user-management-header">
        <DashboardHeader
          title="User Management"
          subtitle="Accounts, access requests, roles and the sign-in audit trail"
          icon={UserCog}
          showAudienceTabs={false}
          badgeCount={badge ? `${badge} pending` : null}
        />
      </div>
      <div className="mx-auto w-full max-w-[1600px] flex-1 space-y-6 p-4 sm:p-6 lg:p-8">{children}</div>
    </div>
  );
}

function UserManagementContent({ meId }: { meId: string | undefined }) {
  const { me } = useCurrentUser();
  const { state: url, update } = useUmUrl();
  const panel = useMotionVariants(fadeUp);

  const stats = useApiQuery(umKeys.stats(STATS_DAYS), (signal) => userManagementApi.stats(STATS_DAYS, signal));
  const audit = useApiQuery(umKeys.audit(AUDIT_LIMIT_MAX), (signal) => userManagementApi.authAudit(AUDIT_LIMIT_MAX, signal));
  const auditEntries = audit.data ?? [];
  const openRequests = stats.data?.kpis.open_requests ?? null;
  const activeSuperadmins = stats.data?.role_mix.find((r) => r.role === "superadmin")?.count ?? 0;

  const drill = useCallback((patch: DeepLinkUpdate) => update({ ...USERS_RESET, ...patch }), [update]);

  const tabs: TabItem<UmTab>[] = [
    { id: "users", label: "Users", icon: Users, badgeCount: stats.data?.kpis.total ?? null },
    { id: "requests", label: "Access Requests", icon: ClipboardList, badgeCount: openRequests },
    { id: "roles", label: "Roles & Permissions", icon: KeyRound },
    { id: "audit", label: "Audit Trail", icon: ScrollText },
  ];

  return (
    <Shell badge={openRequests}>
      <UserOverview
        stats={stats.data}
        statsLoading={stats.isLoading}
        statsError={stats.data ? null : errorMessage(stats.error)}
        onRetry={() => void stats.refetch()}
        audit={auditEntries}
        onDrill={drill}
      />

      <div data-tour="user-management-tabs">
        <TabGroup<UmTab>
          tabs={tabs}
          activeTab={url.tab}
          onChange={(tab) => update({ tab: tab === "users" ? null : tab, user: null, page: null })}
          ariaLabel="User Management sections"
        />
      </div>

      <AnimatePresence mode="wait" initial={false}>
        <m.div key={url.tab} role="tabpanel" aria-label={tabs.find((t) => t.id === url.tab)?.label} variants={panel} initial="hidden" animate="visible" exit="exit">
          {url.tab === "users" ? (
            <UsersTab url={url} update={update} meId={meId} activeSuperadmins={activeSuperadmins} audit={auditEntries} />
          ) : url.tab === "requests" ? (
            <AccessRequestsTab view={url.view} update={update} />
          ) : url.tab === "roles" ? (
            <RolesMatrixTab me={me} />
          ) : (
            <AuthAuditTab
              entries={auditEntries}
              loading={audit.isLoading}
              error={audit.data ? null : errorMessage(audit.error)}
              onRetry={() => void audit.refetch()}
              action={url.action}
              update={update}
            />
          )}
        </m.div>
      </AnimatePresence>
    </Shell>
  );
}
