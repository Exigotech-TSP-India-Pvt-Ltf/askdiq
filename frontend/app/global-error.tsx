"use client";

import "./globals.css";

interface GlobalErrorProps {
  error: Error & { digest?: string };
  reset: () => void;
}

/**
 * Last-resort boundary for errors in the root layout itself (app/error.tsx
 * sits inside that layout and cannot catch them). It renders its own
 * <html>/<body> and uses no context, router or brand hooks, so it renders
 * safely even when the providers above it failed.
 */
export default function GlobalError({ error, reset }: GlobalErrorProps) {
  return (
    <html lang="en">
      <body className="bg-surface-page text-fg">
        <main className="flex min-h-screen items-center justify-center px-4 py-12">
          <div className="w-full max-w-md rounded-2xl border border-line bg-surface-card p-8 text-center shadow-xs">
            <p className="font-mono text-xs font-semibold uppercase tracking-widest text-brand-700">Something went wrong</p>
            <h1 className="mt-2 text-lg font-bold tracking-tight">This page could not be loaded</h1>
            <p className="mt-2 text-sm text-fg-muted">Try again in a moment.</p>
            {error.digest ? (
              <p className="mt-3 text-xs text-fg-subtle">
                Reference <code className="font-mono">{error.digest}</code>
              </p>
            ) : null}
            <div className="mt-6 flex flex-wrap items-center justify-center gap-2">
              <button
                type="button"
                onClick={reset}
                className="rounded-lg bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-700"
              >
                Try again
              </button>
              {/* A plain link: next/link needs the router context this boundary may not have. */}
              {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
              <a href="/" className="rounded-lg border border-line px-3 py-2 text-sm font-medium">
                Go to the start page
              </a>
            </div>
          </div>
        </main>
      </body>
    </html>
  );
}
