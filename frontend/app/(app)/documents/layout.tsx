"use client";

import { Suspense } from "react";
import { RangeProvider } from "@/lib/range-context";

/** Documents share the global date range (citation charts), synced to the URL. */
export default function DocumentsLayout({ children }: { children: React.ReactNode }) {
  return (
    <Suspense fallback={<div className="h-full overflow-y-auto bg-surface-page">{children}</div>}>
      <RangeProvider>{children}</RangeProvider>
    </Suspense>
  );
}
