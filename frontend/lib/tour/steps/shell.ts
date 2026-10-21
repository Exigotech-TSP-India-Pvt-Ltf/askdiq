import type { PageTour } from "../types";

/** "Dashboard basics": the chrome shared by every page (sidebar, account corner, header filters, chat widget). */
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
      body: "The filter at the right end of the tab row shows the active period (for example \"Last 7 days\"). Open it to pick a preset or a custom window, and the charts, KPIs and tables on the page recalculate. Page actions such as Upload document sit just before it.",
      placement: "bottom",
    },
    {
      target: '[data-tour="shell-account"]',
      title: "Your profile menu",
      body: "The sidebar footer shows who is signed in, with your role badge. Click it (or press Enter) to open the menu: Notifications with a count of what's new, Walkthrough guide to replay a tour, Theme (Light, Dark or System) and Sign out. A red dot on your avatar means unread notifications.",
      placement: "right",
    },
    {
      target: '[data-tour="shell-chat-widget"]',
      title: "Ask AskDIQ from any page",
      body: "Open the assistant without leaving the dashboard (Ctrl+/ works too). Your conversation keeps running while you move between pages.",
      placement: "left",
    },
    {
      title: "You're all set",
      body: "Start with the Dashboard for the big picture, then open any page and take its tour to learn what each metric means.",
      placement: "center",
    },
  ],
};
