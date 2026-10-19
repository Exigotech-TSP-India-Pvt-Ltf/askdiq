"use client";

import { Suspense } from "react";
import { RangeProvider } from "@/lib/range-context";

/** Evaluation shares the global date range (also outside /admin), synced to the URL. */
export default function EvaluationLiveLayout({ children }: { children: React.ReactNode }) {
  return (
    <Suspense fallback={<div className="h-full overflow-y-auto bg-surface-page">{children}</div>}>
      <RangeProvider>{children}</RangeProvider>
    </Suspense>
  );
}
