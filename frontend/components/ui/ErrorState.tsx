"use client";

import React from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";
import { CopyButton } from "@/components/ui/CopyButton";
import { secondaryButton } from "@/components/ui/styles";

interface ErrorStateProps {
  title?: string;
  message?: React.ReactNode;
  /** Shown with a copy button so it can be pasted into a ticket. */
  requestId?: string | null;
  onRetry?: () => void;
  className?: string;
  compact?: boolean;
}

/** Standard error state with retry and the request id (newplan §15.1). */
export function ErrorState({
  title = "Something went wrong",
  message,
  requestId,
  onRetry,
  className = "",
  compact = false,
}: ErrorStateProps) {
  return (
    <div
      role="alert"
      className={`flex flex-col items-center justify-center text-center ${compact ? "gap-1.5 px-4 py-6" : "gap-2 px-6 py-12"} ${className}`}
    >
      <span className="flex h-10 w-10 items-center justify-center rounded-full bg-status-danger-bg text-status-danger">
        <AlertTriangle className="h-[18px] w-[18px]" strokeWidth={1.75} aria-hidden="true" />
      </span>
      <p className="text-sm font-semibold text-fg">{title}</p>
      {message ? <p className="max-w-sm text-xs text-fg-muted">{message}</p> : null}
      {requestId ? (
        <p className="flex items-center gap-1 text-[11px] text-fg-subtle">
          <span>Request ID</span>
          <code className="rounded bg-surface-muted px-1.5 py-0.5 font-mono text-fg-muted">{requestId}</code>
          <CopyButton value={requestId} label="Copy request id" />
        </p>
      ) : null}
      {onRetry ? (
        <button type="button" onClick={onRetry} className={`${secondaryButton} mt-2`}>
          <RotateCcw className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
          Try again
        </button>
      ) : null}
    </div>
  );
}
