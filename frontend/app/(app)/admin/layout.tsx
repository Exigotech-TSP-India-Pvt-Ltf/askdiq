"use client";

import { Suspense } from "react";
import { ConsoleAzureBanner } from "@/components/admin/governance/HealthBanner";
import { AudienceProvider } from "@/lib/audience-context";
import { RangeProvider } from "@/lib/range-context";

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return (
    <Suspense fallback={<div className="h-full overflow-y-auto bg-surface-page">{children}</div>}>
      <AudienceProvider>
        <RangeProvider>
          <div className="h-full overflow-y-auto scrollbar-gutter-stable bg-surface-page">
            {/* Azure account Degraded / Unavailable or an active incident (hidden when Available). */}
            <ConsoleAzureBanner className="mx-auto w-full max-w-[1600px] px-6 pt-6 sm:px-8" />
            {children}
          </div>
        </RangeProvider>
      </AudienceProvider>
    </Suspense>
  );
}
