"use client";

import { useEffect } from "react";
import Link from "next/link";
import { RotateCcw } from "lucide-react";
import { BrandedStatusPage } from "@/components/shell/BrandedStatusPage";
import { CopyButton } from "@/components/ui/CopyButton";
import { primaryButton, secondaryButton } from "@/components/ui/styles";

interface ErrorPageProps {
  error: Error & { digest?: string };
  reset: () => void;
}

/** Branded route error boundary: no stack traces, a reference id and a way back. */
export default function ErrorPage({ error, reset }: ErrorPageProps) {
  useEffect(() => {
    // Surface the failure in the browser console for support; the UI shows only the digest.
    console.error(error);
  }, [error]);

  return (
    <BrandedStatusPage
      code="Something went wrong"
      title="This page could not be loaded"
      message={
        <>
          <p>An unexpected error occurred. Try again, or go back to the dashboard.</p>
          {error.digest ? (
            <p className="mt-3 flex items-center justify-center gap-1 text-xs text-fg-subtle">
              Reference
              <code className="rounded-sm bg-surface-muted px-1.5 py-0.5 font-mono text-fg-muted">{error.digest}</code>
              <CopyButton value={error.digest} label="Copy error reference" />
            </p>
          ) : null}
        </>
      }
    >
      <button type="button" onClick={reset} className={primaryButton}>
        <RotateCcw className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
        Try again
      </button>
      <Link href="/admin" className={secondaryButton}>
        Go to the dashboard
      </Link>
    </BrandedStatusPage>
  );
}
