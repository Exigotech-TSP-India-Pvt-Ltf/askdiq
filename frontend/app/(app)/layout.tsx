"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/Sidebar";
import { useAuth } from "@/lib/auth-context";
import { ChatProvider } from "@/lib/chat-context";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { token, isLoading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!isLoading && !token) router.replace("/login");
  }, [isLoading, token, router]);

  if (isLoading || !token) {
    return (
      <div className="flex h-screen items-center justify-center bg-white">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-brand-200 border-t-brand-600" />
      </div>
    );
  }

  return (
    <div className="flex h-screen overflow-hidden bg-white">
      <Sidebar />
      {/* ChatProvider lives here (not inside the chat page) so an in-flight
          query keeps running and its answer isn't lost when navigating to
          Documents/Metrics and back — only this layout, not the page, stays
          mounted across those route changes. */}
      <ChatProvider>
        <main className="flex-1 overflow-hidden">{children}</main>
      </ChatProvider>
    </div>
  );
}

