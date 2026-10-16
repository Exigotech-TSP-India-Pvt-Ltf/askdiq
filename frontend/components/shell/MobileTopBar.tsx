"use client";

import React from "react";
import { Menu } from "lucide-react";
import { BrandLogo } from "@/components/brand/BrandLogo";
import { HeaderUtilities } from "@/components/shell/HeaderUtilities";
import { useMobileNav } from "@/lib/mobile-nav-context";
import { iconButton } from "@/components/ui/styles";

/** App bar below `md`: hamburger for the off-canvas sidebar, logo mark and header utilities. */
export function MobileTopBar() {
  const nav = useMobileNav();
  if (!nav) return null;
  return (
    <header className="flex h-14 shrink-0 items-center justify-between gap-3 border-b border-line bg-surface-card px-4 md:hidden">
      <div className="flex min-w-0 items-center gap-2">
        <button
          type="button"
          onClick={() => nav.setOpen(true)}
          aria-label="Open navigation"
          aria-expanded={nav.open}
          aria-haspopup="dialog"
          className={iconButton}
        >
          <Menu className="h-[18px] w-[18px]" strokeWidth={1.75} aria-hidden="true" />
        </button>
        <BrandLogo variant="mark" />
      </div>
      <HeaderUtilities launcherVariant="mobile" />
    </header>
  );
}
