"use client";

import React, { useEffect, useRef } from "react";
import { usePathname, useRouter } from "next/navigation";
import { m } from "motion/react";
import { useAuth } from "@/lib/auth-context";
import { gateDecision, type GateDecision } from "@/lib/auth-gate";
import { crossfade, fadeUp, useMotionVariants } from "@/lib/motion";
import { AccessRequiredView } from "@/components/auth/AccessRequiredView";
import { SuspendedView } from "@/components/auth/SuspendedView";
import { MascotLoader } from "@/components/ui/MascotLoader";
import { ErrorState } from "@/components/ui/ErrorState";
import { useToast } from "@/components/ui/ToastProvider";

function FullScreenLoader() {
  return (
    <div className="h-screen bg-surface-page">
      <MascotLoader variant="page" label="Checking your session" />
    </div>
  );
}

export interface RouteGate {
  decision: GateDecision;
  /** Full-screen replacement for the whole shell (loading, error, suspended), or null. */
  screen: React.ReactNode | null;
}

/**
 * Route gate for every `(app)` page. No session -> /login
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
  if (decision === "loading" || decision === "login") return { decision, screen: <FullScreenLoader /> };
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
 * On approval the dashboard replaces Access Required at once (no re-login,
 * no navigation) and fades in; the keyed wrapper re-runs the entrance. No
 * exit hold, so the swap never waits on an animation.
 */
export function GateContent({ decision, children }: { decision: GateDecision; children: React.ReactNode }) {
  const enter = useMotionVariants(fadeUp);
  const fade = useMotionVariants(crossfade);
  // Fade the dashboard in only when it replaces Access Required (approval),
  // not on ordinary page loads.
  const wasBlocked = useRef(false);
  const handOver = wasBlocked.current && decision !== "access-required";
  useEffect(() => {
    wasBlocked.current = decision === "access-required";
  }, [decision]);
  if (decision === "access-required") {
    return (
      <m.div key="access-required" className="h-full" variants={fade} initial="hidden" animate="visible">
        <AccessRequiredView />
      </m.div>
    );
  }
  return (
    <m.div key="app" className="h-full" variants={enter} initial={handOver ? "hidden" : false} animate="visible">
      {children}
    </m.div>
  );
}
