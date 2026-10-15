"use client";

import React, { useEffect, useRef } from "react";
import { usePathname, useRouter } from "next/navigation";
import { AnimatePresence, m } from "motion/react";
import { useAuth } from "@/lib/auth-context";
import { gateDecision, type GateDecision } from "@/lib/auth-gate";
import { crossfade, fadeUp, useMotionVariants } from "@/lib/motion";
import { AccessRequiredView } from "@/components/auth/AccessRequiredView";
import { SuspendedView } from "@/components/auth/SuspendedView";
import { ErrorState } from "@/components/ui/ErrorState";
import { useToast } from "@/components/ui/ToastProvider";

function FullScreenSpinner() {
  return (
    <div role="status" aria-label="Loading" className="flex h-screen items-center justify-center bg-surface-page">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-brand-200 border-t-brand-600" />
    </div>
  );
}

export interface RouteGate {
  decision: GateDecision;
  /** Full-screen replacement for the whole shell (loading, error, suspended), or null. */
  screen: React.ReactNode | null;
}

/**
 * Route gate for every `(app)` page (planchanges4 F1.2.3). No session -> /login
 * (with `next`); suspended -> suspended page; pending / rejected -> Chat only,
 * Access Required elsewhere; active -> the full shell. UX only: every API call
 * is authorised by the backend.
 */
export function useRouteGate(): RouteGate {
  const auth = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const toast = useToast();
  const decision = gateDecision(auth.me, pathname, auth.isLoading);
  const failed = !auth.me && !auth.isLoading && Boolean(auth.error);
  const previous = useRef<GateDecision>(decision);

  useEffect(() => {
    if (decision !== "login" || failed) return;
    const next = `${window.location.pathname}${window.location.search}`;
    router.replace(next && next !== "/" ? `/login?next=${encodeURIComponent(next)}` : "/login");
  }, [decision, failed, router]);

  useEffect(() => {
    if (previous.current === "access-required" && decision === "app") {
      toast.show({ title: "Access approved", description: "Welcome to the dashboard.", tone: "success" });
    }
    previous.current = decision;
  }, [decision, toast]);

  if (failed) {
    return {
      decision,
      screen: (
        <div className="flex h-screen items-center justify-center bg-surface-page">
          <ErrorState
            title="We couldn't check your session"
            message="The service may be unavailable. Try again in a moment."
            onRetry={() => void auth.refresh().catch(() => undefined)}
          />
        </div>
      ),
    };
  }
  if (decision === "loading" || decision === "login") return { decision, screen: <FullScreenSpinner /> };
  if (decision === "suspended") {
    return {
      decision,
      screen: <SuspendedView contactEmail={auth.accessContactEmail} onSignOut={() => void auth.logout()} />,
    };
  }
  return { decision, screen: null };
}

/**
 * Page area inside the shell: the route, or Access Required in its place.
 * On approval the Access Required screen hands over to the dashboard with a
 * crossfade (no re-login, no navigation).
 */
export function GateContent({ decision, children }: { decision: GateDecision; children: React.ReactNode }) {
  const enter = useMotionVariants(fadeUp);
  const fade = useMotionVariants(crossfade);
  const blocked = decision === "access-required";
  return (
    <AnimatePresence mode="wait" initial={false}>
      {blocked ? (
        <m.div key="access-required" className="h-full" variants={fade} initial="hidden" animate="visible" exit="exit">
          <AccessRequiredView />
        </m.div>
      ) : (
        <m.div key="app" className="h-full" variants={enter} initial="hidden" animate="visible" exit="exit">
          {children}
        </m.div>
      )}
    </AnimatePresence>
  );
}
