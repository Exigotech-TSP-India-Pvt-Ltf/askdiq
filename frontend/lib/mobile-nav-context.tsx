"use client";

import React, { createContext, useContext, useMemo, useState } from "react";

interface MobileNavValue {
  open: boolean;
  setOpen: (open: boolean) => void;
}

const MobileNavContext = createContext<MobileNavValue | null>(null);

/** Open state of the off-canvas sidebar used below the `md` breakpoint. */
export function MobileNavProvider({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const value = useMemo(() => ({ open, setOpen }), [open]);
  return <MobileNavContext.Provider value={value}>{children}</MobileNavContext.Provider>;
}

/** Null outside MobileNavProvider (no hamburger, no drawer). */
export function useMobileNav(): MobileNavValue | null {
  return useContext(MobileNavContext);
}
