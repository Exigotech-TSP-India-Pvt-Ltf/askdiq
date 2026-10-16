"use client";

import { Suspense } from "react";
import { AudienceProvider } from "@/lib/audience-context";

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return (
    <Suspense fallback={<div className="h-full overflow-y-auto bg-surface-page">{children}</div>}>
      <AudienceProvider>
        <div className="h-full overflow-y-auto [scrollbar-gutter:stable] bg-surface-page">{children}</div>
      </AudienceProvider>
    </Suspense>
  );
}
