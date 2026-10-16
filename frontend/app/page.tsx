"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { hasDashboardAccess } from "@/lib/auth-gate";
import { MascotLoader } from "@/components/ui/MascotLoader";

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
    <div className="h-screen bg-surface-page">
      <MascotLoader variant="page" label="Loading" />
    </div>
  );
}
