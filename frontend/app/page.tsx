"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { hasDashboardAccess } from "@/lib/auth-gate";

/** `/`: dashboard for approved users, Chat for everyone else signed in, otherwise sign-in. */
export default function RootPage() {
  const { me, isLoading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (isLoading) return;
    if (!me) router.replace("/login");
    else router.replace(hasDashboardAccess(me) ? "/admin" : "/chat");
  }, [isLoading, me, router]);

  return (
    <div className="flex h-screen items-center justify-center bg-surface-page">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-brand-200 border-t-brand-600" />
    </div>
  );
}
