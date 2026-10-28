"use client";

import type React from "react";

import Link from "next/link";
import { m, AnimatePresence } from "motion/react";
import {
  AlertTriangle,
  ArrowUpRight,
  FileText,
  MessageSquare,
  ShieldAlert,
  ThumbsUp,
  UserCog,
  type LucideIcon,
} from "lucide-react";
import { EmptyState } from "@/components/ui/EmptyState";
import { RelativeTime } from "@/components/ui/RelativeTime";
import { ErrorState } from "@/components/ui/ErrorState";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { entityHref } from "@/lib/entity-links";
import { fadeUp, useMotionVariants } from "@/lib/motion";
import { isNotImplemented, type ActivityEvent, type ActivityType } from "@/lib/dashboard-api";
import { useDashboardActivity } from "@/lib/use-dashboard";

const TYPE_ICON: Record<ActivityType, LucideIcon> = {
  query: MessageSquare,
  feedback: ThumbsUp,
  error: AlertTriangle,
  guardrail: ShieldAlert,
  document: FileText,
  admin: UserCog,
};

const TYPE_TONE: Record<ActivityType, string> = {
  query: "bg-brand-50 text-brand-700 dark:bg-brand-950/60 dark:text-brand-300",
  feedback: "bg-status-info-bg text-status-info-fg",
  error: "bg-status-danger-bg text-status-danger-fg",
  guardrail: "bg-status-warn-bg text-status-warn-fg",
  document: "bg-surface-muted text-fg-muted",
  admin: "bg-surface-muted text-fg-muted",
};

/** Where an event links: the most specific entity it carries. */
export function activityHref(e: ActivityEvent): string | null {
  if (e.type === "feedback" && e.message_id) return entityHref({ kind: "feedback", id: e.message_id });
  if (e.request_id) return entityHref({ kind: "trace", id: e.request_id });
  if (e.conversation_id) return entityHref({ kind: "conversation", id: e.conversation_id });
  if (e.document_id) return entityHref({ kind: "document", id: e.document_id });
  return null;
}

function EventRow({ event }: { event: ActivityEvent }) {
  const Icon = TYPE_ICON[event.type] ?? MessageSquare;
  const href = activityHref(event);
  const body = (
    <>
      <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-lg ${TYPE_TONE[event.type] ?? TYPE_TONE.admin}`}>
        <Icon className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-xs font-medium text-fg">{event.summary}</span>
        <RelativeTime value={event.at} fallback={event.at} className="text-[11px] text-fg-subtle" />
      </span>
      {href && (
        <ArrowUpRight
          className="h-4 w-4 shrink-0 text-fg-subtle transition-transform duration-150 group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-accent-fg motion-reduce:transition-none"
          strokeWidth={1.75}
          aria-hidden="true"
        />
      )}
    </>
  );
  return href ? (
    <Link
      href={href}
      className="group flex items-center gap-3 rounded-xl px-2 py-2 transition-colors duration-150 hover:bg-surface-muted focus:outline-hidden focus-visible:ring-2 focus-visible:ring-brand-500"
    >
      {body}
    </Link>
  ) : (
    <div className="flex items-center gap-3 px-2 py-2">{body}</div>
  );
}

/** Last 20 events, refreshed every 15 s while the tab is visible. */
export function ActivityFeed() {
  const { data, error, isLoading, refetch } = useDashboardActivity();
  const item = useMotionVariants(fadeUp);
  const missing = isNotImplemented(error);

  let body: React.ReactNode;
  if (isLoading) body = <SkeletonRows rows={5} columns={2} label="Loading activity" />;
  else if (!data && missing)
    body = <EmptyState compact title="No activity feed on this server" description="The server does not provide recent events." />;
  else if (!data && error)
    body = <ErrorState compact title="Couldn't load activity" onRetry={() => void refetch()} />;
  else if (!data || data.items.length === 0) body = <EmptyState compact title="No recent activity" />;
  else
    body = (
      <ul className="max-h-80 space-y-0.5 overflow-y-auto" aria-live="polite">
        <AnimatePresence initial={false}>
          {data.items.map((event) => (
            <m.li key={event.id} variants={item} initial="hidden" animate="visible" exit="exit">
              <EventRow event={event} />
            </m.li>
          ))}
        </AnimatePresence>
      </ul>
    );

  return (
    <section
      aria-labelledby="activity-feed-title"
      className="flex flex-col rounded-2xl border border-brand-300/80 bg-surface-card p-6 shadow-card dark:border-brand-500/25"
    >
      <div className="mb-3 flex items-center justify-between">
        <h2 id="activity-feed-title" className="text-sm font-extrabold tracking-tight text-fg">
          Live activity
        </h2>
        {data && (
          <span className="inline-flex items-center gap-1.5 text-[11px] font-semibold text-status-ok-fg">
            <span className="h-1.5 w-1.5 rounded-full bg-status-ok motion-safe:animate-pulse" aria-hidden="true" />
            Live
          </span>
        )}
      </div>
      {body}
    </section>
  );
}
