"use client";

import React, { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { microsoftLogoColors } from "@/lib/theme-tokens";
import { redirectTo } from "@/lib/navigation";
import { focusRing } from "@/components/ui/styles";

/** Microsoft's four-square logo (inline SVG; colours fixed by Microsoft's guidelines). */
export function MicrosoftLogo({ className = "h-[18px] w-[18px]" }: { className?: string }) {
  return (
    <svg viewBox="0 0 21 21" className={className} aria-hidden="true" focusable="false">
      <rect x="1" y="1" width="9" height="9" fill={microsoftLogoColors.red} />
      <rect x="11" y="1" width="9" height="9" fill={microsoftLogoColors.green} />
      <rect x="1" y="11" width="9" height="9" fill={microsoftLogoColors.blue} />
      <rect x="11" y="11" width="9" height="9" fill={microsoftLogoColors.yellow} />
    </svg>
  );
}

/** Server route that binds the OIDC state cookie and redirects to the IdP. */
export function signInUrl(next: string | null): string {
  return next ? `/auth/login?next=${encodeURIComponent(next)}` : "/auth/login";
}

interface MicrosoftSignInButtonProps {
  /** Already validated same-origin path. */
  next: string | null;
}

/**
 * Full-width "Sign in with Microsoft" (newplan §7.7): 1 px border, rounded-xl,
 * hover lift + brand-tinted border + sheen sweep, focus ring, and a spinner
 * with the button disabled once clicked (no double submits).
 */
export function MicrosoftSignInButton({ next }: MicrosoftSignInButtonProps) {
  const [pending, setPending] = useState(false);

  // Coming back with the browser's Back button (bfcache) must re-enable it.
  useEffect(() => {
    const reset = (event: PageTransitionEvent) => {
      if (event.persisted) setPending(false);
    };
    window.addEventListener("pageshow", reset);
    return () => window.removeEventListener("pageshow", reset);
  }, []);

  function onClick() {
    if (pending) return;
    setPending(true);
    redirectTo(signInUrl(next));
  }

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={pending}
      aria-busy={pending}
      className={`group relative flex w-full items-center justify-center gap-3 overflow-hidden rounded-xl border border-line-strong bg-surface-card px-4 py-3 text-sm font-semibold text-fg shadow-sm transition duration-150 hover:-translate-y-0.5 hover:border-brand-400 hover:shadow-md disabled:translate-y-0 disabled:cursor-wait disabled:opacity-80 disabled:shadow-sm motion-reduce:transition-none motion-reduce:hover:translate-y-0 dark:hover:border-brand-500 ${focusRing}`}
    >
      {/* Sheen sweep on hover (decorative; hidden for reduced motion). */}
      <span
        aria-hidden="true"
        className="pointer-events-none absolute inset-y-0 -left-1/2 w-1/2 -skew-x-12 bg-gradient-to-r from-transparent via-brand-100/60 to-transparent opacity-0 transition-[transform,opacity] duration-700 group-hover:translate-x-[300%] group-hover:opacity-100 group-disabled:hidden motion-reduce:hidden dark:via-white/10"
      />
      {pending ? (
        <Loader2 className="h-[18px] w-[18px] animate-spin text-brand-600 dark:text-brand-400" strokeWidth={1.75} aria-hidden="true" />
      ) : (
        <MicrosoftLogo />
      )}
      <span>{pending ? "Redirecting to Microsoft…" : "Sign in with Microsoft"}</span>
    </button>
  );
}
