"use client";

import React, { useEffect, useId, useRef } from "react";
import { ShieldCheck } from "lucide-react";
import { primaryButton, secondaryButton } from "@/components/ui/styles";
import { useAuth } from "@/lib/auth-context";
import { hasDashboardAccess } from "@/lib/auth-gate";
import { useVisibilityPolling } from "@/lib/use-visibility-polling";
import { ACCESS_POLL_INTERVAL_MS } from "@/components/auth/AccessRequiredView";

/** True when a signed-in user has no approved role and so may not use the app or chatbot. */
export function useChatbotBlocked(): boolean {
  const { me } = useAuth();
  return Boolean(me) && !hasDashboardAccess(me);
}

/**
 * Full-screen access dialog shown in place of the whole app (rendered on the
 * first paint, no shell behind it) until the user is granted one of the roles.
 * Polls /auth/me so the app opens once access is granted.
 */
export function ChatbotAccessScreen() {
  const { refresh, logout, accessContactEmail } = useAuth();
  const contact = accessContactEmail;
  const subject = "AskDIQ Dashboard Access Request";
  const body = [
    "Hi,",
    "",
    "I have signed in to AskDIQ with my Microsoft account but don't have access to the chatbot yet.",
    "Could you please grant me access?",
  ].join("\n");
  const mailto = contact ? `mailto:${contact}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}` : null;
  const titleId = useId();
  const descId = useId();
  const emailRef = useRef<HTMLAnchorElement>(null);

  useEffect(() => {
    emailRef.current?.focus();
  }, []);

  useVisibilityPolling(() => {
    refresh().catch(() => undefined);
  }, ACCESS_POLL_INTERVAL_MS);

  return (
    <div className="flex h-screen items-center justify-center bg-surface-page p-4">
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descId}
        className="flex aspect-square w-full max-w-[380px] flex-col items-center justify-center rounded-2xl border border-line bg-surface-card p-8 text-center shadow-xl"
      >
        <span className="flex h-16 w-16 items-center justify-center rounded-2xl bg-teal-50 text-teal-700 ring-8 ring-teal-50/50 dark:bg-teal-900/30 dark:text-teal-300">
          <ShieldCheck className="h-8 w-8" strokeWidth={1.75} aria-hidden="true" />
        </span>
        <h2 id={titleId} className="mt-6 text-lg font-semibold tracking-tight text-fg">
          Access Approval Required
        </h2>
        <p className="mt-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-fg-muted">AskDIQ Assistant</p>
        <p id={descId} className="mt-4 text-xs leading-relaxed text-fg-muted">
          Your account is signed in but doesn&apos;t have chatbot access yet. Please contact{" "}
          {contact && mailto ? (
            <a href={mailto} className="font-semibold text-fg underline underline-offset-2">
              {contact}
            </a>
          ) : (
            "your administrator"
          )}{" "}
          to request access. This page opens automatically once access is granted.
        </p>
        <div className={`mt-6 grid w-full gap-2 ${mailto ? "grid-cols-2" : "grid-cols-1"}`}>
          <button type="button" onClick={() => void logout()} className={`${secondaryButton} justify-center`}>
            Cancel
          </button>
          {mailto ? (
            <a ref={emailRef} href={mailto} className={`${primaryButton} justify-center`}>
              Email for access
            </a>
          ) : null}
        </div>
      </div>
    </div>
  );
}
