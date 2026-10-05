"use client";

import { createContext, useContext, useMemo, type ReactNode } from "react";
import type { UserOut } from "./types";

// Login/registration were removed entirely — every visitor shares this
// fixed guest identity so downstream code (Sidebar, chat/session/governance
// calls) that expects a `token`/`user` keeps working unchanged, with no
// real auth flow behind it.
const GUEST_USER: UserOut = {
  id: "00000000-0000-0000-0000-000000000000",
  full_name: "Guest",
  email: "default@local",
  created_at: new Date(0).toISOString(),
};
const GUEST_TOKEN = "no-auth";

interface AuthContextValue {
  token: string | null;
  user: UserOut | null;
  isLoading: boolean;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const value = useMemo<AuthContextValue>(
    () => ({
      token: GUEST_TOKEN,
      user: GUEST_USER,
      isLoading: false,
      // No session exists to end — kept as a no-op so the Sidebar's
      // "Sign out" button doesn't need to change or error.
      logout: () => {},
    }),
    []
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}

