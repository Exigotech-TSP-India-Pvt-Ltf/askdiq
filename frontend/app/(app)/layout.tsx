"use client";

import { Sidebar } from "@/components/Sidebar";
import { ChatProvider } from "@/lib/chat-context";
import { AudienceProvider } from "@/lib/audience-context";
import { MobileNavProvider } from "@/lib/mobile-nav-context";
import { CommandCenter } from "@/components/shell/CommandCenter";
import { ConsoleRuntime } from "@/components/shell/ConsoleRuntime";
import { MobileTopBar } from "@/components/shell/MobileTopBar";
import { GateContent, useRouteGate } from "@/components/auth/RouteGate";
import { AskDiqLauncher } from "@/components/askdiq/AskDiqLauncher";
import { TourProvider } from "@/components/tour/TourProvider";
import { TourLauncher } from "@/components/tour/TourLauncher";
import { ChatbotAccessScreen, useChatbotBlocked } from "@/components/auth/ChatbotAccessScreen";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  // Route gate: login redirect, suspended page, Access Required in
  // place of dashboard routes for users without an approved role.
  const gate = useRouteGate();
  const chatbotBlocked = useChatbotBlocked();
  if (gate.screen) return <>{gate.screen}</>;
  // Signed in without one of the roles: only the access dialog, no shell behind it.
  if (chatbotBlocked) return <ChatbotAccessScreen />;

  return (
    <MobileNavProvider>
      {/* Guided tours: outside CommandCenter so the palette can launch them. */}
      <TourProvider>
        <CommandCenter>
          {/* Audience wraps the sidebar too: its links prefetch audience-scoped data. */}
          <AudienceProvider>
          {/* Cache restore, live stream and background preload (renders nothing; ahead of the page). */}
          <ConsoleRuntime />
          <div className="flex h-screen overflow-hidden bg-white dark:bg-dark-bg">
            {/* Permission-filtered: users without a role see Chat only. */}
            <Sidebar />
            <div className="flex min-w-0 flex-1 flex-col">
              {/* Below md the sidebar is off-canvas; this bar holds the hamburger. */}
              <MobileTopBar />
              {/* ChatProvider lives here (not inside the chat page) so an in-flight
                  query keeps running and its answer isn't lost when navigating to
                  Documents/Metrics and back — only this layout, not the page, stays
                  mounted across those route changes. */}
                <ChatProvider>
                  <main className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden bg-surface-page">
                    <GateContent decision={gate.decision}>{children}</GateContent>
                  </main>
                  {/* Floating AskDIQ chat on every dashboard page (drawer is lazy-loaded). */}
                  <AskDiqLauncher />
                  {/* Tour launcher for pages without a header slot (hidden while a header one is mounted). */}
                  <TourLauncher variant="floating" />
                </ChatProvider>
            </div>
          </div>
          </AudienceProvider>
        </CommandCenter>
      </TourProvider>
    </MobileNavProvider>
  );
}
