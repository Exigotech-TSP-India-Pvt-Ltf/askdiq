"use client";

import React, { useId, useState } from "react";
import Link from "next/link";
import { m } from "motion/react";
import { Check, Clock3, LockKeyhole, MessageSquare, Send, X } from "lucide-react";
import { DashboardHeader } from "@/components/admin/DashboardHeader";
import { useAuth } from "@/lib/auth-context";
import { useBrand } from "@/lib/brand-context";
import {
  JUSTIFICATION_MAX_LENGTH,
  createAccessRequest,
  fetchAccessRequestState,
  type AccessRequest,
  type AccessRequestState,
} from "@/lib/auth-api";
import { hasDashboardAccess } from "@/lib/auth-gate";
import { ROLE_LABELS, type Role } from "@/lib/permissions";
import { useApiQuery, errorStatus, HTTP_CONFLICT } from "@/lib/query-cache";
import { fadeUp, pop, progressFill, stagger, useMotionVariants, usePrefersReducedMotion } from "@/lib/motion";
import { useVisibilityPolling } from "@/lib/use-visibility-polling";
import { useToast } from "@/components/ui/ToastProvider";
import { focusRing, primaryButton, secondaryButton } from "@/components/ui/styles";

/** `/auth/me` is re-read every 15 s while waiting (paused when the tab is hidden). */
export const ACCESS_POLL_INTERVAL_MS = 15_000;
export const ACCESS_REQUEST_QUERY_KEY = "auth/access-request";

const REQUESTABLE_ROLES: Role[] = ["operator", "admin"];

type StepState = "done" | "current" | "upcoming" | "failed";

interface Step {
  label: string;
  state: StepState;
}

/** Stepper model: requested -> under review -> approved (rejected marks review as failed). */
export function accessSteps(request: AccessRequest | null, approved: boolean): Step[] {
  if (approved) {
    return [
      { label: "Requested", state: "done" },
      { label: "Under review", state: "done" },
      { label: "Approved", state: "done" },
    ];
  }
  if (!request) {
    return [
      { label: "Requested", state: "current" },
      { label: "Under review", state: "upcoming" },
      { label: "Approved", state: "upcoming" },
    ];
  }
  if (request.status === "rejected") {
    return [
      { label: "Requested", state: "done" },
      { label: "Not approved", state: "failed" },
      { label: "Approved", state: "upcoming" },
    ];
  }
  return [
    { label: "Requested", state: "done" },
    { label: "Under review", state: "current" },
    { label: "Approved", state: request.status === "approved" ? "done" : "upcoming" },
  ];
}

const DOT: Record<StepState, string> = {
  done: "border-brand-600 bg-brand-600 text-white",
  current: "border-brand-500 bg-surface-card text-brand-600 ring-4 ring-brand-500/15 dark:text-brand-400",
  upcoming: "border-line-strong bg-surface-card text-fg-subtle",
  failed: "border-status-danger bg-status-danger-bg text-status-danger",
};

function StepIcon({ state, index }: { state: StepState; index: number }) {
  if (state === "done") return <Check className="h-4 w-4" strokeWidth={2} aria-hidden="true" />;
  if (state === "failed") return <X className="h-4 w-4" strokeWidth={2} aria-hidden="true" />;
  if (state === "current") return <Clock3 className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />;
  return <span className="text-xs font-semibold">{index + 1}</span>;
}

export function AccessStepper({ steps }: { steps: Step[] }) {
  const reduced = usePrefersReducedMotion();
  const dot = useMotionVariants(pop);
  // The rail reaches the furthest step that is not still upcoming.
  const reached = steps.reduce((last, step, i) => (step.state === "upcoming" ? last : i), 0);
  const progress = steps.length > 1 ? reached / (steps.length - 1) : 0;

  return (
    <ol
      aria-label="Access request progress"
      data-testid="access-stepper"
      data-motion={reduced ? "static" : "animated"}
      className="relative grid grid-cols-3"
    >
      <span aria-hidden="true" className="absolute left-[16.66%] right-[16.66%] top-4 h-0.5 rounded-full bg-line" />
      {reduced ? (
        <span
          aria-hidden="true"
          className="absolute left-[16.66%] right-[16.66%] top-4 h-0.5 origin-left rounded-full bg-brand-600"
          style={{ transform: `scaleX(${progress})` }}
        />
      ) : (
        <m.span
          aria-hidden="true"
          className="absolute left-[16.66%] right-[16.66%] top-4 h-0.5 origin-left rounded-full bg-brand-600"
          initial={{ scaleX: 0 }}
          animate={{ scaleX: progress }}
          transition={progressFill}
        />
      )}
      {steps.map((step, index) => (
        <li
          key={step.label}
          className="relative flex flex-col items-center gap-2 text-center"
          aria-current={step.state === "current" ? "step" : undefined}
          data-state={step.state}
        >
          {reduced ? (
            <span className={`flex h-8 w-8 items-center justify-center rounded-full border-2 ${DOT[step.state]}`}>
              <StepIcon state={step.state} index={index} />
            </span>
          ) : (
            <m.span
              key={step.state}
              variants={dot}
              initial="hidden"
              animate="visible"
              className={`flex h-8 w-8 items-center justify-center rounded-full border-2 ${DOT[step.state]}`}
            >
              <StepIcon state={step.state} index={index} />
            </m.span>
          )}
          <span className={`text-xs font-medium ${step.state === "upcoming" ? "text-fg-subtle" : "text-fg"}`}>{step.label}</span>
        </li>
      ))}
    </ol>
  );
}

function RequestForm({ onCreated, resubmit }: { onCreated: () => void; resubmit: boolean }) {
  const toast = useToast();
  const roleId = useId();
  const justificationId = useId();
  const [role, setRole] = useState<Role | "">("");
  const [justification, setJustification] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [failed, setFailed] = useState(false);

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    setFailed(false);
    try {
      await createAccessRequest({
        requested_role: role || null,
        justification: justification.trim() || null,
      });
      toast.show({ title: "Access requested", description: "An administrator will review it.", tone: "success" });
      onCreated();
    } catch (error) {
      if (errorStatus(error) === HTTP_CONFLICT) {
        toast.show({ title: "A request is already open", tone: "info" });
        onCreated();
      } else {
        setFailed(true);
      }
    } finally {
      setSubmitting(false);
    }
  }

  const field = `mt-1.5 w-full rounded-lg border border-line bg-surface-card px-3 py-2 text-sm text-fg placeholder:text-fg-subtle ${focusRing}`;

  return (
    <form onSubmit={onSubmit} className="space-y-4" aria-label="Request dashboard access">
      <div>
        <label htmlFor={roleId} className="text-xs font-semibold text-fg">
          Role
        </label>
        <select id={roleId} value={role} onChange={(e) => setRole(e.target.value as Role | "")} className={field}>
          <option value="">Let the administrator decide</option>
          {REQUESTABLE_ROLES.map((r) => (
            <option key={r} value={r}>
              {ROLE_LABELS[r]}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label htmlFor={justificationId} className="text-xs font-semibold text-fg">
          Why do you need access? <span className="font-normal text-fg-subtle">(optional)</span>
        </label>
        <textarea
          id={justificationId}
          value={justification}
          onChange={(e) => setJustification(e.target.value.slice(0, JUSTIFICATION_MAX_LENGTH))}
          maxLength={JUSTIFICATION_MAX_LENGTH}
          rows={3}
          className={`${field} resize-y`}
          aria-describedby={`${justificationId}-count`}
        />
        <p id={`${justificationId}-count`} className="mt-1 text-right text-[11px] text-fg-subtle">
          {justification.length}/{JUSTIFICATION_MAX_LENGTH}
        </p>
      </div>
      {failed ? (
        <p role="alert" className="text-xs text-status-danger-fg">
          Your request couldn&apos;t be sent. Please try again.
        </p>
      ) : null}
      <button type="submit" disabled={submitting} aria-busy={submitting} className={primaryButton}>
        <Send className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
        {resubmit ? "Request access again" : "Request access"}
      </button>
    </form>
  );
}

/**
 * Shown in place of any dashboard route for signed-in users without an
 * approved role. Polls `/auth/me`; the
 * route gate swaps in the dashboard as soon as the status flips to active,
 * with no re-login.
 */
export function AccessRequiredView() {
  const { me, refresh, accessContactEmail } = useAuth();
  const brand = useBrand();
  const container = useMotionVariants(stagger);
  const item = useMotionVariants(fadeUp);
  const query = useApiQuery<AccessRequestState>(ACCESS_REQUEST_QUERY_KEY, (signal) => fetchAccessRequestState(signal), {
    staleTime: 0,
  });
  const request = query.data?.request ?? null;
  const approved = hasDashboardAccess(me);
  const contact = accessContactEmail ?? query.data?.access_contact_email ?? null;
  const showForm = !query.isLoading && (!request || request.status === "rejected");

  useVisibilityPolling(() => {
    refresh()
      .then((next) => {
        if (!hasDashboardAccess(next)) void query.refetch();
      })
      .catch(() => undefined);
  }, ACCESS_POLL_INTERVAL_MS);

  return (
    <div className="flex h-full flex-col overflow-y-auto bg-surface-page">
      <DashboardHeader title="Access required" subtitle="Dashboard access needs approval" icon={LockKeyhole} showAudienceTabs={false} />
      <m.section
        variants={container}
        initial="hidden"
        animate="visible"
        aria-labelledby="access-required-title"
        className="mx-auto w-full max-w-2xl px-4 py-10 sm:px-6"
      >
        <m.div variants={item} className="rounded-2xl border border-line bg-surface-card p-6 shadow-xs sm:p-8">
          <h2 id="access-required-title" className="text-lg font-bold tracking-tight text-fg">
            {approved ? "Access approved" : "You don't have dashboard access yet"}
          </h2>
          <p className="mt-2 text-sm text-fg-muted">
            Signed in as <span className="font-medium text-fg">{me?.user.email}</span>. {brand.productName} dashboards open once a{" "}
            {brand.companyName} administrator approves your access. Chat is available now.
          </p>

          <div className="mt-8">
            <AccessStepper steps={accessSteps(request, approved)} />
          </div>

          <div className="mt-8 border-t border-line pt-6">
            {request?.status === "rejected" && request.decision_note ? (
              <p className="mb-4 rounded-lg bg-status-danger-bg px-3 py-2 text-xs text-status-danger-fg">
                Reviewer note: {request.decision_note}
              </p>
            ) : null}
            {showForm ? (
              <RequestForm resubmit={request?.status === "rejected"} onCreated={() => void query.refetch()} />
            ) : request?.status === "pending" ? (
              <div role="status" className="text-sm text-fg-muted">
                <p>
                  Your request{request.requested_role ? ` for the ${ROLE_LABELS[request.requested_role]} role` : ""} is waiting for review.
                  This page updates on its own when it is approved.
                </p>
              </div>
            ) : null}
          </div>

          <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
            <p className="text-xs text-fg-subtle">
              Questions? Contact{" "}
              {contact ? (
                <a
                  href={`mailto:${contact}`}
                  className={`rounded-sm font-medium text-brand-700 underline-offset-4 hover:underline dark:text-brand-300 ${focusRing}`}
                >
                  {contact}
                </a>
              ) : (
                "your administrator"
              )}
              .
            </p>
            <Link href="/chat" className={secondaryButton}>
              <MessageSquare className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
              Open Chat
            </Link>
          </div>
        </m.div>
      </m.section>
    </div>
  );
}
