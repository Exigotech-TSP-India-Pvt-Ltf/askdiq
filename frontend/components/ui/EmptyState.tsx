import React from "react";
import { Inbox } from "lucide-react";

interface EmptyStateProps {
  title: string;
  description?: React.ReactNode;
  icon?: React.ComponentType<{ className?: string; strokeWidth?: string | number }>;
  /** Optional call to action (a button or link). */
  action?: React.ReactNode;
  className?: string;
  /** Smaller padding for cards, popovers and table bodies. */
  compact?: boolean;
}

/** Standard "nothing here yet" state for cards, charts, tables and lists. */
export function EmptyState({ title, description, icon: Icon = Inbox, action, className = "", compact = false }: EmptyStateProps) {
  return (
    <div
      className={`flex flex-col items-center justify-center text-center ${compact ? "gap-1.5 px-4 py-6" : "gap-2 px-6 py-12"} ${className}`}
    >
      <span className="flex h-10 w-10 items-center justify-center rounded-full border border-line-brand/60 bg-surface-studio text-accent-fg dark:border-brand-500/25">
        <Icon className="h-[18px] w-[18px]" strokeWidth={1.75} aria-hidden="true" />
      </span>
      <p className="text-sm font-semibold text-fg">{title}</p>
      {description ? <p className="max-w-sm text-xs text-fg-muted">{description}</p> : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}
