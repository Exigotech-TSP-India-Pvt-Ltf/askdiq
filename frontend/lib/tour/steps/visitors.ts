import type { PageTour } from "../types";

export const visitorsTour: PageTour = {
  id: "visitors",
  title: "Visitors",
  steps: [
    {
      title: "Welcome to Visitors",
      body: "Visitors shows who is using the assistant, identified by client IP: how many, how often they return, when they are active and what each one did. Use it to understand reach and to keep internal test traffic apart from real users.",
      placement: "center",
    },
    {
      target: '[data-tour="visitors-header"]',
      title: "Audience and date range",
      body: "The audience tabs decide which visitors count: Overview is everyone, Users excludes test IPs, Testing shows only test IPs. The date range drives the cards and charts. Try Users to see production visitors only.",
      placement: "bottom",
    },
    {
      target: '[data-tour="visitors-kpis"]',
      title: "Visitor KPIs",
      body: "Visitors are distinct IPs active on at least one day in the range. New visitors had their first-ever session in the range; Returning is the rest. Active visitors now counts those with a query in the last few minutes, whatever the range.",
      placement: "bottom",
    },
    {
      target: '[data-tour="visitors-active-visitors"]',
      title: "Active visitors: DAU / WAU / MAU",
      body: "Distinct end users with at least one query in the last 1, 7 and 30 days, identified by client id (or by IP when there is none), with test IPs excluded. Always as of now, independent of the date range; the trend compares with the previous equal window.",
      placement: "bottom",
    },
    {
      target: '[data-tour="visitors-new-vs-returning"]',
      title: "New vs returning",
      body: "One bar per UTC day: new visitors started their first-ever session that day, returning ones came back. A returning visitor appears on every day they were active, so the bars don't add up to the Visitors card.",
      placement: "top",
    },
    {
      target: '[data-tour="visitors-heatmap"]',
      title: "When visitors are active",
      body: "Sessions started per weekday and hour, in UTC; darker cells are your busy times. Try this: click a cell to filter the table to visitors active in that slot, then clear the chip above the table to undo.",
      placement: "top",
    },
    {
      target: '[data-tour="visitors-filters"]',
      title: "Search and filters",
      body: "Search by part of an IP address, and filter by status or by visitor type (Production or Test). A heatmap selection shows here as an \"Active in\" chip you can clear.",
      placement: "bottom",
    },
    {
      target: '[data-tour="visitors-table"]',
      title: "Visitor table",
      body: "One row per client IP with all-time sessions, queries, feedback, first seen and last active. Sort by sessions, queries, first seen or last active to find your heaviest or newest visitors.",
      placement: "top",
    },
    {
      target: '[data-tour="visitors-ip-link"]',
      title: "Drill into a visitor",
      body: "Click an IP (or anywhere on its row) to open the visitor detail: all-time stat cards, cost in the range, a session timeline, and their sessions, queries and feedback. From there, select a query to open the inspector or a session to read the conversation.",
      placement: "right",
    },
    {
      target: '[data-tour="visitors-row-sessions"]',
      title: "View sessions",
      body: "Opens Usage & Cost on the Sessions tab, filtered to this IP, listing every session it started.",
      placement: "left",
    },
    {
      target: '[data-tour="visitors-row-mark-test"]',
      title: "Mark as test IP",
      body: "Flags this IP as internal test traffic in one click, with Undo in the toast. Its whole history moves to the Testing audience on every page, because the classification is applied at query time.",
      placement: "left",
    },
    {
      target: '[data-tour="visitors-manage-test-ips"]',
      title: "Manage test IPs",
      body: "Add or remove test IP rules (single IPs or CIDR ranges such as 10.0.0.0/24) with a note on who they belong to. Built-in loopbacks and configured defaults are listed but can't be removed. Changes re-classify historical data immediately.",
      placement: "bottom",
    },
    {
      target: '[data-tour="visitors-pagination"]',
      title: "Pagination",
      body: "Page through visitors and choose 10, 25, 50 or 100 rows per page.",
      placement: "top",
    },
    {
      title: "That's Visitors",
      body: "Keep test traffic tagged so the Users audience stays honest across every page. Related pages: Usage & Cost for sessions and spend, Conversation Logs for what visitors asked, and the Dashboard for the overall picture.",
      placement: "center",
    },
  ],
};
