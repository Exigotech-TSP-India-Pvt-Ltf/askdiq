import type { PageTour } from "../types";

/** "Dashboard basics": the chrome shared by every page (sidebar, header tools, chat widget). */
export const shellTour: PageTour = {
  id: "shell",
  title: "Dashboard basics",
  steps: [
    {
      title: "Welcome to the AskDIQ dashboard",
      body: "This one-minute tour covers the controls you'll find on every page: navigation, filters, search, notifications and the assistant. Use ← and → to move, and Esc to leave.",
      placement: "center",
    },
    {
      target: '[data-tour="shell-nav"]',
      title: "Navigate between pages",
      body: "Every page you have access to is listed here; what you see depends on your role. Collapse the sidebar with the arrow at the top to give charts more room.",
      placement: "right",
    },
    {
      target: '[data-tour="shell-command-palette"]',
      title: "Go to any page",
      body: "Click Go to page, or press Ctrl+K (⌘K on Mac) from anywhere, to type a page name and jump straight there; you can start tours from it too. Press ? at any time for the full list of keyboard shortcuts.",
      placement: "right",
    },
    {
      target: '[data-tour="shell-audience"]',
      title: "Choose the audience",
      body: "Overview shows all traffic, Users shows real end users only, and Testing shows internal test traffic. The choice carries across pages, so numbers stay comparable.",
      placement: "bottom",
    },
    {
      target: '[data-tour="shell-date-range"]',
      title: "Set the date range",
      body: "Pick a preset or a custom window, and the charts, KPIs and tables on the page recalculate for that period. Try the last 7 days to spot recent changes.",
      placement: "bottom",
    },
    {
      target: '[data-tour="shell-notifications"]',
      title: "Notifications",
      body: "Access requests and incidents land here, with a badge counting what's new. Open an item to go straight to the page that needs attention.",
      placement: "bottom",
    },
    {
      target: '[data-tour="shell-profile"]',
      title: "Profile, theme and sign out",
      body: "Open your account menu to see your role, switch between light and dark theme, and sign out.",
      placement: "bottom",
    },
    {
      target: '[data-tour="shell-account"]',
      title: "Your account at a glance",
      body: "The sidebar footer shows who is signed in (name, email and role badge), with the theme switch and sign out on the row beneath. In the collapsed sidebar, hover the avatar for the same details.",
      placement: "right",
    },
    {
      target: '[data-tour="shell-chat-widget"]',
      title: "Ask AskDIQ from any page",
      body: "Open the assistant without leaving the dashboard (Ctrl+/ works too). Your conversation keeps running while you move between pages.",
      placement: "left",
    },
    {
      target: '[data-tour="shell-tour-launcher"]',
      title: "Replay a tour any time",
      body: "This compass opens the tours: \"Tour this page\" explains the current page card by card, and \"Dashboard basics\" brings you back here.",
      placement: "bottom",
    },
    {
      title: "You're all set",
      body: "Start with the Dashboard for the big picture, then open any page and take its tour to learn what each metric means.",
      placement: "center",
    },
  ],
};
