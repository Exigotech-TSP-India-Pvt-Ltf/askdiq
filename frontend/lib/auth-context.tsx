"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { UserOut } from "./types";
import { fetchMe, logoutRequest, type AuthMe } from "./auth-api";
import { queryCache } from "./query-cache";
import { clearPersistedQueryCache } from "./query-persist";
import { hasPermission, type Permission, type Role, type UserStatus } from "./permissions";
import { redirectTo } from "./navigation";

/**
 * Opaque "signed in" marker kept in `token` so existing pages that guard on
 * `if (!token) return` keep working. It is NOT the session: the session id
 * lives only in the httpOnly cookie, which client JavaScript can never read.
 */
export const SESSION_MARKER = "session";

interface AuthContextValue {
  /** `SESSION_MARKER` when signed in, otherwise null (never the cookie value). */
  token: string | null;
  user: UserOut | null;
  isLoading: boolean;
  logout: () => Promise<void>;
  me: AuthMe | null;
  role: Role | null;
  status: UserStatus | null;
  permissions: readonly Permission[];
  accessContactEmail: string | null;
  /** Set when `/auth/me` failed for a reason other than 401 (backend down). */
  error: unknown;
  /** Re-reads `/auth/me` (access-required polling, after approval). */
  refresh: () => Promise<AuthMe | null>;
}

const NO_PERMISSIONS: readonly Permission[] = [];

function toUser(me: AuthMe): UserOut {
  return { id: me.user.id, full_name: me.user.name || me.user.email, email: me.user.email, created_at: "" };
}

function valueFor(
  me: AuthMe | null,
  isLoading: boolean,
  error: unknown,
  logout: () => Promise<void>,
  refresh: () => Promise<AuthMe | null>,
): AuthContextValue {
  return {
    token: me ? SESSION_MARKER : null,
    user: me ? toUser(me) : null,
    isLoading,
    logout,
    me,
    role: me?.role ?? null,
    status: me?.status ?? null,
    permissions: me?.permissions ?? NO_PERMISSIONS,
    accessContactEmail: me?.access_contact_email ?? null,
    error,
    refresh,
  };
}

let signingOut: Promise<void> | null = null;

/**
 * Signs out (the route revokes the server session and clears the cookie),
 * then replaces the current history entry with /login so Back cannot return
 * to the signed-in page. Bounded by LOGOUT_CLIENT_TIMEOUT_MS and single-flight:
 * a hung backend or a double click never leaves the user stuck signed in.
 */
function signOut(): Promise<void> {
  if (signingOut) return signingOut;
  signingOut = (async () => {
    try {
      await logoutRequest();
    } catch {
      // Timed out or failed: leave for /login regardless.
    }
    // No cached console data survives a sign-out (memory or this tab's storage).
    clearPersistedQueryCache();
    queryCache.clear();
    redirectTo("/login", { replace: true });
  })().finally(() => {
    signingOut = null;
  });
  return signingOut;
}

const SIGNED_OUT: AuthContextValue = valueFor(null, false, undefined, signOut, async () => null);

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

interface AuthProviderProps {
  children: ReactNode;
  /** Skips the first `/auth/me` fetch (tests, server-provided state). */
  initialMe?: AuthMe | null;
}

export function AuthProvider({ children, initialMe }: AuthProviderProps) {
  const [me, setMe] = useState<AuthMe | null>(initialMe ?? null);
  const [isLoading, setIsLoading] = useState(initialMe === undefined);
  const [error, setError] = useState<unknown>(undefined);
  const inflight = useRef<Promise<AuthMe | null> | null>(null);

  const refresh = useCallback(async (): Promise<AuthMe | null> => {
    // Concurrent callers (gate + polling) share one request.
    if (inflight.current) return inflight.current;
    const run = (async () => {
      try {
        const next = await fetchMe();
        setMe(next);
        setError(undefined);
        return next;
      } catch (err) {
        setError(err);
        throw err;
      } finally {
        setIsLoading(false);
        inflight.current = null;
      }
    })();
    inflight.current = run;
    return run;
  }, []);

  useEffect(() => {
    if (initialMe !== undefined) return;
    refresh().catch(() => undefined);
  }, [initialMe, refresh]);

  // Back/Forward cache: a page restored after sign-out must not stay usable.
  // Re-check the session; a 401 sends the route gate to /login.
  useEffect(() => {
    const onPageShow = (event: PageTransitionEvent) => {
      if (event.persisted) refresh().catch(() => undefined);
    };
    window.addEventListener("pageshow", onPageShow);
    return () => window.removeEventListener("pageshow", onPageShow);
  }, [refresh]);

  const value = useMemo(() => valueFor(me, isLoading, error, signOut, refresh), [me, isLoading, error, refresh]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

/** Current auth state; signed out (no network) when rendered outside AuthProvider. */
export function useAuth(): AuthContextValue {
  return useContext(AuthContext) ?? SIGNED_OUT;
}

/**
 * True when the signed-in user's role grants `permission`. Fails closed: false
 * while `/auth/me` is loading, when signed out, or for an unknown permission.
 * This is the canonical permission hook; feature PRs' temporary `useCan`
 * shims are replaced by it (or its `useCan` alias).
 */
export function usePermission(permission: Permission): boolean {
  return hasPermission(useAuth().permissions, permission);
}

/** Alias of `usePermission` (same signature) for code written against `useCan`. */
export const useCan = usePermission;

interface RequirePermissionProps {
  permission: Permission;
  children: ReactNode;
  /** Rendered instead when the permission is missing (default: nothing). */
  fallback?: ReactNode;
}

/** Renders `children` only for users whose role grants `permission`. */
export function RequirePermission({ permission, children, fallback = null }: RequirePermissionProps) {
  return <>{usePermission(permission) ? children : fallback}</>;
}
