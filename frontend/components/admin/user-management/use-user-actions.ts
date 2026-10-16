"use client";

/**
 * Mutations for the Users tab and drawer: optimistic role / status changes
 * with rollback, session revocation and invites. Every 409 (stale version,
 * self-change, last superadmin, duplicate email) becomes a toast carrying the
 * server's reason; the affected queries are refetched by `useApiMutation`.
 */
import { useCallback } from "react";
import { HTTP_CONFLICT, errorStatus, useApiMutation, type MutationConflict } from "@/lib/query-cache";
import { useToast } from "@/components/ui/ToastProvider";
import { PENDING_ACCESS_QUERY_KEY } from "@/lib/use-shell-notifications";
import {
  umKeys,
  userManagementApi,
  type Role,
  type UserAccount,
  type UserAccountDetail,
  type UserAccountPage,
  ROLE_LABEL,
} from "@/lib/user-management-api";

export interface UserChangeVars {
  user: UserAccount;
  role?: Role;
  status?: "active" | "suspended";
}

const STALE_HINT = "changed by someone else";

export function useConflictToast() {
  const toast = useToast();
  return useCallback(
    ({ error }: MutationConflict<unknown>) => {
      const detail = error instanceof Error ? error.message : "";
      const stale = detail.toLowerCase().includes(STALE_HINT);
      toast.show({
        title: stale ? "Changed by someone else" : "Change not applied",
        description: stale ? "The latest version has been loaded; review it and try again." : detail,
        tone: stale ? "info" : "error",
      });
    },
    [toast],
  );
}

function errorText(error: unknown): string {
  return error instanceof Error && error.message ? error.message : "Request failed.";
}

// Activating a pending user closes their request: refresh the sidebar badge too.
const AFTER_CHANGE = [
  umKeys.statsPrefix,
  umKeys.userPrefix,
  umKeys.auditPrefix,
  umKeys.requestsPrefix,
  PENDING_ACCESS_QUERY_KEY,
] as const;

function applyChange<T extends UserAccount>(user: T, vars: UserChangeVars): T {
  return { ...user, role: vars.role ?? user.role, status: vars.status ?? user.status };
}

/**
 * Role / status change, applied optimistically to `key` (the Users page
 * shown, which the drawer also reads, or the user's detail entry when the user
 * is not on that page) and rolled back on failure.
 */
export function useUserChange(key: string) {
  const toast = useToast();
  const onConflict = useConflictToast();

  return useApiMutation<UserChangeVars, UserAccount, UserAccountPage | UserAccountDetail>({
    mutationFn: ({ user, role, status }) =>
      userManagementApi.updateUser(user.id, {
        version: user.version,
        ...(role ? { role } : {}),
        ...(status ? { status } : {}),
      }),
    optimistic: {
      key,
      update: (current, vars) => {
        if (!current) return current as unknown as UserAccountDetail;
        if ("items" in current) {
          return { ...current, items: current.items.map((u) => (u.id === vars.user.id ? applyChange(u, vars) : u)) };
        }
        return current.id === vars.user.id ? applyChange(current, vars) : current;
      },
    },
    invalidate: [umKeys.usersPrefix, ...AFTER_CHANGE],
    onConflict,
    onSuccess: (updated, vars) => {
      const what = vars.role
        ? `Role set to ${ROLE_LABEL[vars.role]}`
        : vars.status === "suspended"
          ? "User suspended"
          : "User reactivated";
      toast.show({ title: what, description: updated.email, tone: "success" });
    },
    onError: (error) => {
      if (errorStatus(error) === HTTP_CONFLICT) return;
      toast.show({ title: "Change failed", description: errorText(error), tone: "error" });
    },
  });
}

export function useRevokeSessions() {
  const toast = useToast();
  const onConflict = useConflictToast();
  return useApiMutation<UserAccount, { revoked: number }, UserAccountDetail>({
    mutationFn: (user) => userManagementApi.revokeSessions(user.id),
    invalidate: [umKeys.userPrefix, umKeys.auditPrefix],
    onConflict,
    onSuccess: (result, user) =>
      toast.show({
        title: `Revoked ${result.revoked} session${result.revoked === 1 ? "" : "s"}`,
        description: `${user.email} is signed out everywhere.`,
        tone: "success",
      }),
    onError: (error) => {
      if (errorStatus(error) === HTTP_CONFLICT) return;
      toast.show({ title: "Could not revoke sessions", description: errorText(error), tone: "error" });
    },
  });
}

export interface InviteVars {
  email: string;
  role: Role;
  name?: string;
}

export function useInvite() {
  const toast = useToast();
  const onConflict = useConflictToast();
  return useApiMutation<InviteVars, UserAccount>({
    mutationFn: (vars) => userManagementApi.invite(vars),
    invalidate: [umKeys.usersPrefix, umKeys.statsPrefix, umKeys.auditPrefix],
    onConflict,
    onSuccess: (user) =>
      toast.show({
        title: "Invitation created",
        description: `${user.email} can sign in with SSO as ${user.role ? ROLE_LABEL[user.role] : "a user"}.`,
        tone: "success",
      }),
    onError: (error) => {
      if (errorStatus(error) === HTTP_CONFLICT) return;
      toast.show({ title: "Invite failed", description: errorText(error), tone: "error" });
    },
  });
}
