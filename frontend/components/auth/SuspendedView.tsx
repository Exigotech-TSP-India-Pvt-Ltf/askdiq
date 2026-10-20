"use client";

import React from "react";
import Link from "next/link";
import { LogOut, ShieldOff } from "lucide-react";
import { BrandLogo } from "@/components/brand/BrandLogo";
import { useBrand } from "@/lib/brand-context";
import { focusRing, primaryButton, secondaryButton } from "@/components/ui/styles";

interface SuspendedViewProps {
  contactEmail: string | null;
  /** Shows "Sign out" (session present) instead of "Back to sign in". */
  onSignOut?: () => void;
}

/** Account-suspended page: no shell, only the contact route. */
export function SuspendedView({ contactEmail, onSignOut }: SuspendedViewProps) {
  const brand = useBrand();
  return (
    <main className="flex min-h-screen items-center justify-center bg-surface-page px-4 py-12 text-fg">
      <div className="w-full max-w-md rounded-2xl border border-line bg-surface-card p-8 text-center shadow-sm">
        <div className="flex justify-center">
          <BrandLogo variant="full" />
        </div>
        <span className="mx-auto mt-8 flex h-11 w-11 items-center justify-center rounded-full bg-status-danger-bg text-status-danger">
          <ShieldOff className="h-5 w-5" strokeWidth={1.75} aria-hidden="true" />
        </span>
        <h1 className="mt-4 text-lg font-bold tracking-tight text-fg">Your account is suspended</h1>
        <p className="mt-2 text-sm text-fg-muted">
          You can&apos;t use {brand.productName} while your account is suspended. To restore access, contact{" "}
          {contactEmail ? (
            <a
              href={`mailto:${contactEmail}`}
              className={`rounded font-medium text-brand-700 underline-offset-4 hover:underline dark:text-brand-300 ${focusRing}`}
            >
              {contactEmail}
            </a>
          ) : (
            `your ${brand.companyName} administrator`
          )}
          .
        </p>
        <div className="mt-6 flex justify-center">
          {onSignOut ? (
            <button type="button" onClick={onSignOut} className={secondaryButton}>
              <LogOut className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
              Sign out
            </button>
          ) : (
            <Link href="/login" className={primaryButton}>
              Back to sign in
            </Link>
          )}
        </div>
      </div>
    </main>
  );
}
