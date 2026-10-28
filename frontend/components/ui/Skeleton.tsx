import React from "react";
import { studioSurface } from "@/components/ui/styles";

interface SkeletonProps {
  className?: string;
}

/** A single shimmering placeholder block (decorative; wrap groups in SkeletonGroup). */
export function Skeleton({ className = "h-4 w-full" }: SkeletonProps) {
  return (
    <span
      aria-hidden="true"
      className={`block animate-pulse rounded-md bg-surface-muted motion-reduce:animate-none dark:bg-brand-500/8 ${className}`}
    />
  );
}

interface SkeletonGroupProps {
  /** Announced to screen readers while loading. */
  label?: string;
  className?: string;
  children: React.ReactNode;
}

/** Loading container: one polite announcement instead of many empty blocks. */
export function SkeletonGroup({ label = "Loading", className = "", children }: SkeletonGroupProps) {
  return (
    <div role="status" aria-busy="true" aria-label={label} className={className}>
      {children}
      <span className="sr-only">{label}</span>
    </div>
  );
}

/** Lines of text. */
export function SkeletonText({ lines = 3, label }: { lines?: number; label?: string }) {
  return (
    <SkeletonGroup label={label} className="space-y-2">
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} className={`h-3 ${i === lines - 1 ? "w-2/3" : "w-full"}`} />
      ))}
    </SkeletonGroup>
  );
}

/** KPI / chart card placeholder. */
export function SkeletonCard({ label, className = "" }: { label?: string; className?: string }) {
  return (
    <SkeletonGroup label={label} className={`space-y-3 p-5 ${studioSurface} ${className}`}>
      <Skeleton className="h-3 w-1/3" />
      <Skeleton className="h-7 w-1/2" />
      <Skeleton className="h-24 w-full" />
    </SkeletonGroup>
  );
}

/** Table rows placeholder. */
export function SkeletonRows({ rows = 5, columns = 4, label }: { rows?: number; columns?: number; label?: string }) {
  return (
    <SkeletonGroup label={label} className="divide-y divide-line">
      {Array.from({ length: rows }, (_, r) => (
        <div key={r} className="flex gap-4 px-4 py-3">
          {Array.from({ length: columns }, (_, c) => (
            <Skeleton key={c} className="h-3 flex-1" />
          ))}
        </div>
      ))}
    </SkeletonGroup>
  );
}
