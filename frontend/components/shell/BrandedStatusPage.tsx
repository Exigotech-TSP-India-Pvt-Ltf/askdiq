import React from "react";
import { BrandLogo } from "@/components/brand/BrandLogo";

interface BrandedStatusPageProps {
  code: string;
  title: string;
  message: React.ReactNode;
  children?: React.ReactNode;
}

/** Shared layout for the branded 404 / error pages (newplan §15.1). */
export function BrandedStatusPage({ code, title, message, children }: BrandedStatusPageProps) {
  return (
    <main className="flex min-h-screen items-center justify-center bg-surface-page px-4 py-12 text-fg">
      <div className="w-full max-w-md rounded-2xl border border-line bg-surface-card p-8 text-center shadow-sm">
        <div className="flex justify-center">
          <BrandLogo variant="full" />
        </div>
        <p className="mt-8 font-mono text-xs font-semibold uppercase tracking-widest text-brand-700 dark:text-brand-300">{code}</p>
        <h1 className="mt-2 text-lg font-bold tracking-tight text-fg">{title}</h1>
        <div className="mt-2 text-sm text-fg-muted">{message}</div>
        {children ? <div className="mt-6 flex flex-wrap items-center justify-center gap-2">{children}</div> : null}
      </div>
    </main>
  );
}
