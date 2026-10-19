"use client";

import React, { useState } from "react";
import { Loader2, LogOut } from "lucide-react";
import { ThemeToggle } from "@/components/ThemeToggle";
import { RoleBadge } from "@/components/shell/RoleBadge";
import { focusRing } from "@/components/ui/styles";
import { useAuth } from "@/lib/auth-context";
import { initials } from "@/lib/format";
import { ROLE_LABELS } from "@/lib/permissions";

const avatarClass = "flex shrink-0 items-center justify-center rounded-full bg-brand-600 font-semibold text-white shadow-sm ring-2 ring-surface-card";

/** Email on one line with a single trailing ellipsis; the full address is in the tooltip. */
function EmailLine({ email }: { email: string }) {
  return (
    <p title={email} data-testid="account-email" className="truncate text-[11px] leading-4 text-fg-subtle">
      {email}
    </p>
  );
}

/**
 * Sidebar footer: who is signed in (name, email, role) with the theme toggle
 * and sign out on their own row, so the identity text is never squeezed.
 * Collapsed, it stacks a tooltip avatar above the two icon actions.
 */
export function SidebarAccount({ collapsed }: { collapsed: boolean }) {
  const { user, logout, role } = useAuth();
  const [signingOut, setSigningOut] = useState(false);
  const name = user?.full_name || "Signed out";
  const email = user?.email ?? "";
  const roleLabel = role ? ROLE_LABELS[role] : null;

  function signOut() {
    if (signingOut) return;
    setSigningOut(true);
    // logout() always ends on /login (bounded wait); reset only if it ever returns here.
    void Promise.resolve(logout()).finally(() => setSigningOut(false));
  }

  const signOutButton = (
    <button
      type="button"
      onClick={signOut}
      disabled={signingOut}
      aria-busy={signingOut}
      title={signingOut ? "Signing out…" : "Sign out"}
      aria-label="Sign out"
      className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-ink/10 bg-surface-card text-fg-muted shadow-sm transition-colors hover:border-status-danger/40 hover:bg-status-danger-bg hover:text-status-danger-fg disabled:cursor-wait disabled:opacity-60 dark:border-white/10 dark:bg-dark-surface ${focusRing}`}
    >
      {signingOut ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <LogOut className="h-4 w-4" strokeWidth={2} aria-hidden="true" />}
    </button>
  );

  if (collapsed) {
    const summary = [name, email, roleLabel].filter(Boolean).join("\n");
    return (
      <div data-tour="shell-account" className="flex flex-col items-center gap-2 border-t border-line p-3">
        <div role="img" title={summary} aria-label={summary.replace(/\n/g, ", ")} className={`${avatarClass} h-9 w-9 text-xs`}>
          {user ? initials(user.full_name) : "—"}
        </div>
        <ThemeToggle size="sm" />
        {signOutButton}
      </div>
    );
  }

  return (
    <div data-tour="shell-account" className="border-t border-line px-2 py-3">
      <div className="rounded-xl border border-line bg-surface-muted/50 p-2 dark:bg-white/[0.03]">
        <div className="flex items-center gap-2">
          <div aria-hidden="true" className={`${avatarClass} h-9 w-9 text-xs`}>
            {user ? initials(user.full_name) : "—"}
          </div>
          <div className="min-w-0 flex-1">
            <p title={name} className="truncate text-sm font-semibold leading-5 text-fg">
              {name}
            </p>
            {email ? <EmailLine email={email} /> : null}
          </div>
        </div>
        <div className="mt-2.5 flex items-center justify-between gap-2 border-t border-line/70 pt-2.5">
          {role ? <RoleBadge role={role} /> : <span />}
          <div className="flex items-center gap-1.5">
            <ThemeToggle size="sm" />
            {signOutButton}
          </div>
        </div>
      </div>
    </div>
  );
}
