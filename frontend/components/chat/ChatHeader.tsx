"use client";

import { BrandLogo } from "@/components/brand/BrandLogo";
import { useAuth } from "@/lib/auth-context";
import { useBrand } from "@/lib/brand-context";

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  return (parts[0][0] + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
}

/** Signed-in identity chip (newplan §7.7): initials, name and email. */
export function UserChip() {
  const { user } = useAuth();
  if (!user) return null;
  const name = user.full_name || user.email;
  return (
    <div
      className="flex min-w-0 items-center gap-2 rounded-full border border-line bg-surface-card py-1 pl-1 pr-3"
      aria-label={`Signed in as ${name}`}
      data-testid="user-chip"
    >
      <span
        className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-brand-50 text-[11px] font-bold text-brand-700 dark:bg-brand-950/60 dark:text-brand-300"
        aria-hidden="true"
      >
        {initials(name)}
      </span>
      <span className="hidden min-w-0 flex-col leading-tight sm:flex">
        <span className="truncate text-xs font-semibold text-fg">{name}</span>
        {user.email && user.email !== name && <span className="truncate text-[11px] text-fg-subtle">{user.email}</span>}
      </span>
    </div>
  );
}

/** Branded chat header (planchanges4 F3.5, newplan §1 Chat row). */
export function ChatHeader() {
  const brand = useBrand();
  return (
    <header className="flex h-20 shrink-0 items-center justify-between gap-4 border-b border-line bg-surface-card px-6">
      <div className="flex min-w-0 items-center gap-3">
        <BrandLogo variant="mark" />
        <div className="min-w-0">
          <h1 className="truncate text-sm font-semibold text-fg">{brand.productName} Assistant</h1>
          <p className="truncate text-xs text-fg-subtle">
            Answers are grounded in your knowledge base and checked before delivery.
          </p>
        </div>
      </div>
      <UserChip />
    </header>
  );
}
