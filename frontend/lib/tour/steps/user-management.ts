import type { PageTour } from "../types";

const t = (id: string): string => `[data-tour="user-management-${id}"]`;

export const userManagementTour: PageTour = {
  id: "user-management",
  title: "User Management",
  steps: [
    {
      title: "User Management",
      body: "This is where superadmins decide who can open the dashboard and with which role. Approve access requests, change roles, suspend leavers and audit every sign-in from here.",
      placement: "center",
    },
    {
      target: t("header"),
      title: "Pending requests badge",
      body: "When people are waiting for access, an \"N pending\" badge appears next to the title. The same count shows on the User Management item in the sidebar and in the notification bell.",
      placement: "bottom",
    },
    {
      target: t("kpis"),
      title: "Account KPIs",
      body: "Total, Active, Pending and Suspended are all-time account counts by status; Logins (24h) counts successful sign-ins in the last 24 hours. Sparklines cover the last 14 UTC days, and Pending turns amber while someone waits. Try this: click a card to open the matching filtered list.",
      placement: "bottom",
    },
    {
      target: t("logins-chart"),
      title: "Logins per day",
      body: "One bar per UTC day for the last 14 days, counting successful sign-in events (not distinct people). Click a bar to open Users sorted by most recent login.",
      placement: "bottom",
    },
    {
      target: t("funnel"),
      title: "Access-request funnel",
      body: "How people move from Signed in to Requested, Approved and Active, all time. Active can exceed Approved because invited users skip the request. Click a stage to filter Users by that status.",
      placement: "bottom",
    },
    {
      target: t("role-distribution"),
      title: "Role distribution",
      body: "Active users split by role: Operator, Admin and Superadmin. Click a slice to list the active users with that role.",
      placement: "top",
    },
    {
      target: t("time-to-approve"),
      title: "Time to approve",
      body: "How long approved requests waited over the last 30 days, from under 1 hour to over 3 days. A heavy right side means people wait too long. Click a bin to see the decisions.",
      placement: "top",
    },
    {
      target: t("tabs"),
      title: "Four sections",
      body: "Users lists every account, Access Requests is the approval queue, Roles & Permissions shows what each role can do, and Audit Trail records sign-ins and admin actions.",
      placement: "bottom",
    },
    {
      target: t("users-table"),
      title: "Users",
      body: "Everyone who has signed in or been invited, with role, status, last login and approver. Search by name or email, filter by role or status, and sort any column.",
      placement: "top",
      tab: "users",
    },
    {
      target: t("users-table"),
      title: "User drawer: change role, suspend",
      body: "Click a row to open the user drawer with profile, live sessions and history. Its Actions panel lets you change the role, suspend (locked out on their next request), reactivate or revoke sessions. Changing a pending user's role does not grant access: approve them in Access Requests.",
      placement: "top",
    },
    {
      target: t("invite"),
      title: "Invite user",
      body: "Pre-provision a colleague with a role before they first sign in, so they skip the access request.",
      placement: "left",
    },
    {
      target: t("requests-table"),
      title: "Approve or reject requests",
      body: "People who signed in without a role, oldest first. Pick a role and Approve, or Reject with a note they will see. Tick rows to bulk-approve up to 100 at once, or focus a row and press A to approve, X to reject.",
      placement: "top",
      tab: "requests",
    },
    {
      target: t("requests-view"),
      title: "Queue or history",
      body: "Switch between Pending, Approved, Rejected and All to see who decided what, and when.",
      placement: "left",
    },
    {
      target: t("roles-matrix"),
      title: "Roles & permissions",
      body: "A read-only matrix of what each role can do; the API enforces exactly this table. If your live permissions differ, a warning appears at the top.",
      placement: "top",
      tab: "roles",
    },
    {
      target: t("audit-table"),
      title: "Audit trail",
      body: "Sign-ins, denied sign-ins, approvals, role changes, suspensions and session revocations, newest first, with actor and IP. Search by actor or target email. Try this: the Logins (24h) card opens this tab filtered to successful sign-ins.",
      placement: "top",
      tab: "audit",
    },
    {
      title: "You're set",
      body: "Clear the Pending badge often, suspend leavers promptly and use the Audit Trail to answer \"who did what\". Related: Settings for branding and the insights taxonomy, Usage for how people use the assistant.",
      placement: "center",
    },
  ],
};
