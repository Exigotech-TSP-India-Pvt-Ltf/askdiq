"use client";

/**
 * User detail drawer (planchanges4 F1.4.2): profile, live sessions, recent
 * activity and audit entries, plus change role, suspend / reactivate and
 * revoke sessions. Self-changes and demoting or suspending the last active
 * superadmin are blocked with the reason shown as a tooltip (the API enforces
 * the same rules and answers 409).
 */
import React, { useEffect, useState } from "react";
import { History, KeyRound, LogOut, MonitorSmartphone, ShieldCheck, UserRoundCheck, UserRoundX } from "lucide-react";
import { DrawerShell } from "@/components/ui/DrawerShell";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { SkeletonText } from "@/components/ui/Skeleton";
import { dangerButton, primaryButton, secondaryButton } from "@/components/ui/styles";
import { useApiQuery } from "@/lib/query-cache";
import {
  AUTH_EVENT_LABEL,
  ROLES,
  ROLE_LABEL,
  isAuthEvent,
  umKeys,
  userManagementApi,
  type AuditLogEntry,
  type Role,
  type UserAccount,
} from "@/lib/user-management-api";
import { GuardedButton, RoleBadge, StatusBadge, fieldClass } from "./parts";
import { RelativeTime } from "@/components/ui/RelativeTime";
import { useRevokeSessions, useUserChange } from "./use-user-actions";

export const SELF_CHANGE_REASON = "You cannot change your own role or status.";
export const LAST_SUPERADMIN_REASON = "The last active superadmin cannot be demoted or suspended.";
export const ROLE_REQUIRED_REASON = "Assign a role before reactivating this user.";

const RECENT_LIMIT = 8;

type Pending = { kind: "role"; role: Role } | { kind: "suspend" } | { kind: "reactivate" } | { kind: "revoke" } | null;

interface UserDrawerProps {
  userId: string | null;
  /** The row from the Users page shown (carries optimistic updates). */
  row: UserAccount | undefined;
  /** Cache key of that page, or null when the user is not on it. */
  listKey: string | null;
  meId: string | undefined;
  activeSuperadmins: number;
  onClose: () => void;
  onPrev?: () => void;
  onNext?: () => void;
}

function Section({ title, icon: Icon, children }: { title: string; icon: typeof History; children: React.ReactNode }) {
  return (
    <section className="space-y-2.5">
      <h3 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-fg-muted">
        <Icon className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
        {title}
      </h3>
      {children}
    </section>
  );
}

/** One-line summary of an auth audit row's `detail` (fields from app/auth/admin_service.py). */
export function auditSummary(entry: AuditLogEntry): string {
  const d = entry.detail;
  const parts: string[] = [];
  if (typeof d.target_email === "string") parts.push(d.target_email);
  if ("from_role" in d || "to_role" in d) {
    parts.push(`${String(d.from_role ?? "none")} → ${String(d.to_role ?? "none")}`);
  } else if ("from_status" in d || "to_status" in d) {
    parts.push(`${String(d.from_status ?? "?")} → ${String(d.to_status ?? "?")}`);
  } else if (typeof d.role === "string") parts.push(d.role);
  if (typeof d.revoked === "number") parts.push(`${d.revoked} revoked`);
  if (typeof d.note === "string" && d.note) parts.push(`“${d.note}”`);
  return parts.join(" · ");
}

function AuditList({ entries, empty }: { entries: readonly AuditLogEntry[]; empty: string }) {
  if (entries.length === 0) return <EmptyState compact title={empty} icon={History} />;
  return (
    <ol className="divide-y divide-line rounded-xl border border-line">
      {entries.map((e) => (
        <li key={e.id} className="flex items-start justify-between gap-3 px-3 py-2 text-xs">
          <div className="min-w-0">
            <p className="font-medium text-fg">{isAuthEvent(e.action) ? AUTH_EVENT_LABEL[e.action] : e.action}</p>
            <p className="truncate text-fg-subtle">
              {e.actor}
              {auditSummary(e) ? ` · ${auditSummary(e)}` : ""}
            </p>
          </div>
          <RelativeTime value={e.created_at} className="shrink-0 text-fg-subtle" />
        </li>
      ))}
    </ol>
  );
}

export function UserDrawer({
  userId,
  row,
  listKey,
  meId,
  activeSuperadmins,
  onClose,
  onPrev,
  onNext,
}: UserDrawerProps) {
  const open = userId !== null;
  const detailQuery = useApiQuery(open ? umKeys.user(userId) : null, (signal) =>
    userManagementApi.getUser(userId ?? "", signal),
  );
  const detail = detailQuery.data;
  // The list row carries optimistic role / status; the detail adds sessions.
  const user: UserAccount | undefined = row ?? detail;
  const version = Math.max(row?.version ?? 0, detail?.version ?? 0);

  const change = useUserChange(listKey ?? umKeys.user(userId ?? ""));
  const revoke = useRevokeSessions();
  const [pending, setPending] = useState<Pending>(null);
  const [roleChoice, setRoleChoice] = useState<Role>("operator");

  useEffect(() => {
    if (user?.role) setRoleChoice(user.role);
  }, [user?.id, user?.role]);

  const isSelf = Boolean(user && meId && user.id === meId);
  const isLastSuperadmin = Boolean(
    user && user.role === "superadmin" && user.status === "active" && activeSuperadmins <= 1,
  );
  const guardReason = isSelf ? SELF_CHANGE_REASON : isLastSuperadmin ? LAST_SUPERADMIN_REASON : null;
  const roleReason = isSelf ? SELF_CHANGE_REASON : isLastSuperadmin && roleChoice !== "superadmin" ? LAST_SUPERADMIN_REASON : null;
  const reactivateReason = isSelf ? SELF_CHANGE_REASON : user && !user.role ? ROLE_REQUIRED_REASON : null;

  // Precomputed per user by the detail endpoint (newest first).
  const activity = (detail?.id === user?.id ? detail?.recent_activity ?? [] : []).slice(0, RECENT_LIMIT);
  const entries = (detail?.id === user?.id ? detail?.recent_audit ?? [] : []).slice(0, RECENT_LIMIT);

  async function confirm() {
    if (!user || !pending) return;
    const current = { ...user, version };
    setPending(null);
    if (pending.kind === "role") await change.mutate({ user: current, role: pending.role });
    else if (pending.kind === "suspend") await change.mutate({ user: current, status: "suspended" });
    else if (pending.kind === "reactivate") await change.mutate({ user: current, status: "active" });
    else await revoke.mutate(current);
  }

  const busy = change.isPending || revoke.isPending;

  return (
    <>
      <DrawerShell
        open={open}
        onClose={onClose}
        onPrev={onPrev}
        onNext={onNext}
        title={user ? user.name || user.email : "User"}
        description={user?.email}
        headerExtra={user ? <StatusBadge status={user.status} /> : null}
        widthClassName="max-w-xl"
      >
        {!user && detailQuery.isLoading ? (
          <SkeletonText lines={6} label="Loading user" />
        ) : !user && detailQuery.error ? (
          <ErrorState compact message={(detailQuery.error as Error).message} onRetry={() => void detailQuery.refetch()} />
        ) : user ? (
          <>
            <Section title="Profile" icon={ShieldCheck}>
              <dl className="grid grid-cols-2 gap-x-4 gap-y-2 rounded-xl border border-line bg-surface-card p-4 text-xs">
                <dt className="text-fg-subtle">Role</dt>
                <dd>
                  <RoleBadge role={user.role} />
                </dd>
                <dt className="text-fg-subtle">Status</dt>
                <dd>
                  <StatusBadge status={user.status} />
                </dd>
                <dt className="text-fg-subtle">Last login</dt>
                <dd className="text-fg">
                  <RelativeTime value={user.last_login_at} display="absolute" />
                </dd>
                <dt className="text-fg-subtle">Created</dt>
                <dd className="text-fg">
                  <RelativeTime value={user.created_at} display="absolute" />
                </dd>
                <dt className="text-fg-subtle">Approved by</dt>
                <dd className="truncate text-fg">{user.approved_by_email ?? "—"}</dd>
                <dt className="text-fg-subtle">Approved</dt>
                <dd className="text-fg">
                  <RelativeTime value={user.approved_at} display="absolute" />
                </dd>
              </dl>
            </Section>

            <Section title="Actions" icon={KeyRound}>
              <div data-tour="user-management-drawer-actions" className="space-y-3 rounded-xl border border-line bg-surface-card p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <label htmlFor="um-role-select" className="text-xs text-fg-muted">
                    Role
                  </label>
                  <select
                    id="um-role-select"
                    value={roleChoice}
                    onChange={(e) => setRoleChoice(e.target.value as Role)}
                    disabled={isSelf}
                    className={fieldClass}
                  >
                    {ROLES.map((r) => (
                      <option key={r} value={r}>
                        {ROLE_LABEL[r]}
                      </option>
                    ))}
                  </select>
                  <GuardedButton
                    className={primaryButton}
                    disabled={busy || roleChoice === user.role}
                    disabledReason={roleReason}
                    onClick={() => setPending({ kind: "role", role: roleChoice })}
                  >
                    Change role
                  </GuardedButton>
                </div>
                <div className="flex flex-wrap gap-2">
                  {user.status === "suspended" ? (
                    <GuardedButton
                      className={secondaryButton}
                      disabled={busy}
                      disabledReason={reactivateReason}
                      onClick={() => setPending({ kind: "reactivate" })}
                    >
                      <UserRoundCheck className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                      Reactivate
                    </GuardedButton>
                  ) : (
                    <GuardedButton
                      className={dangerButton}
                      disabled={busy}
                      disabledReason={guardReason}
                      onClick={() => setPending({ kind: "suspend" })}
                    >
                      <UserRoundX className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                      Suspend
                    </GuardedButton>
                  )}
                  <GuardedButton className={secondaryButton} disabled={busy} onClick={() => setPending({ kind: "revoke" })}>
                    <LogOut className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                    Revoke sessions
                  </GuardedButton>
                </div>
              </div>
            </Section>

            <Section title="Live sessions" icon={MonitorSmartphone}>
              {detailQuery.isLoading && !detail ? (
                <SkeletonText lines={2} label="Loading sessions" />
              ) : (detail?.active_sessions.length ?? 0) === 0 ? (
                <EmptyState compact title="No active sessions" icon={MonitorSmartphone} />
              ) : (
                <ul className="divide-y divide-line rounded-xl border border-line" data-testid="um-sessions">
                  {detail?.active_sessions.map((s) => (
                    <li key={s.id} className="space-y-0.5 px-3 py-2 text-xs">
                      <p className="flex justify-between gap-2">
                        <span className="font-mono text-fg">{s.ip_address ?? "unknown IP"}</span>
                        <RelativeTime value={s.last_seen_at} prefix="last seen " className="text-fg-subtle" />
                      </p>
                      <p className="truncate text-fg-subtle">{s.user_agent ?? "unknown client"}</p>
                      <p className="text-fg-subtle">
                        Started <RelativeTime value={s.created_at} /> · expires <RelativeTime value={s.expires_at} />
                      </p>
                    </li>
                  ))}
                </ul>
              )}
            </Section>

            <Section title="Recent activity" icon={History}>
              <AuditList entries={activity} empty="No recent activity" />
            </Section>

            <Section title="Audit entries" icon={ShieldCheck}>
              <AuditList entries={entries} empty="No audit entries about this user" />
            </Section>
          </>
        ) : null}
      </DrawerShell>

      <ConfirmDialog
        open={pending?.kind === "role"}
        title="Change role"
        description={
          user && pending?.kind === "role"
            ? `${user.email} becomes ${ROLE_LABEL[pending.role]}. It takes effect on their next request.`
            : undefined
        }
        confirmLabel="Change role"
        onConfirm={confirm}
        onCancel={() => setPending(null)}
      />
      <ConfirmDialog
        open={pending?.kind === "reactivate"}
        title="Reactivate user"
        description={user ? `${user.email} regains access with their current role.` : undefined}
        confirmLabel="Reactivate"
        onConfirm={confirm}
        onCancel={() => setPending(null)}
      />
      <ConfirmDialog
        open={pending?.kind === "suspend"}
        tone="danger"
        title="Suspend user"
        description={user ? `${user.email} loses access immediately on their next request.` : undefined}
        confirmText={user?.email}
        confirmLabel="Suspend"
        onConfirm={confirm}
        onCancel={() => setPending(null)}
      />
      <ConfirmDialog
        open={pending?.kind === "revoke"}
        tone="danger"
        title="Revoke all sessions"
        description={user ? `${user.email} is signed out on every device.` : undefined}
        confirmText={user?.email}
        confirmLabel="Revoke sessions"
        onConfirm={confirm}
        onCancel={() => setPending(null)}
      />
    </>
  );
}
