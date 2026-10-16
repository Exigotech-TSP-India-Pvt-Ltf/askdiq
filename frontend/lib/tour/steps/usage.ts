import type { PageTour } from "../types";

const t = (id: string): string => `[data-tour="usage-${id}"]`;

export const usageTour: PageTour = {
  id: "usage",
  title: "Usage & Cost",
  steps: [
    {
      title: "Usage & Cost",
      body: "This page answers what the assistant costs, where the tokens go and who drives the spend. Admins and finance owners check spend and the forecast; support uses it to find and audit a single turn. All money is in USD.",
      placement: "center",
    },
    {
      target: t("tabs"),
      title: "Four views",
      body: "Overview is the live economics studio. All Users, All Sessions and All Queries drill down from visitor IP, to session, to a single turn: clicking a row opens the next tab already filtered.",
      placement: "bottom",
      tab: "overview",
    },
    {
      target: `${t("cost-insights")} > :nth-child(1)`,
      title: "Cost by stage",
      body: "Daily USD per pipeline stage: query and guardrail embeddings, uncached and cached prompt, reasoning, visible output and ingestion. Try View as table to read exact values per day.",
      placement: "bottom",
    },
    {
      target: `${t("cost-insights")} > :nth-child(2)`,
      title: "Month-end forecast",
      body: "Cumulative spend this month plus a dashed linear projection to month end. If a monthly budget is configured it appears as a reference line, so an overrun is visible early.",
      placement: "bottom",
    },
    {
      target: t("net-spend"),
      title: "Net platform spend",
      body: "Total billed cost of every turn in the selected audience, at the prices stored when each answer was produced. Switch the audience (Overview, Users, Testing) in the header to exclude test traffic.",
      placement: "bottom",
    },
    {
      target: t("cost-per-turn"),
      title: "Cost per turn",
      body: "Net spend divided by the number of turns: what one question and its answer cost on average. Watch it after prompt or model changes.",
      placement: "bottom",
    },
    {
      target: t("waterfall"),
      title: "Economic waterfall",
      body: "Splits spend into LLM generation (prompt, reasoning, output), embeddings and the money saved by the prompt cache. The split uses today's rates, so it can differ slightly from the stored total.",
      placement: "right",
    },
    {
      target: t("token-rings"),
      title: "Billed tokens and cache",
      body: "Three rings show prompt, hidden reasoning and visible output as shares of all billed tokens; the centre is the billed total in thousands. Reasoning is billed at the output rate even though users never see it. The Cache-hit ratio gauge above shows how much of the prompt was served cheaper from cache.",
      placement: "top",
    },
    {
      target: t("latency-cockpit"),
      title: "Latency",
      body: "P50 means half of all turns finished faster than this; P95 is the slow tail. The speedometer shows both (the dial tops out at 4 s), next to the share of answers with verified citations. The Median Turn Speed tile above repeats P50.",
      placement: "left",
    },
    {
      target: t("feed"),
      title: "Live audit feed",
      body: "The eight most recent turns with their cost and tokens. Click a row or Inspect Trace to open the Single-Query Economic Inspector.",
      placement: "top",
    },
    {
      target: t("users-stats"),
      title: "All Users",
      body: "Spend grouped by visitor IP, with the top consumers highlighted. Search by IP, filter Production or Test users, and click any IP to jump to its sessions.",
      placement: "bottom",
      tab: "users",
    },
    {
      target: t("sessions-table"),
      title: "All Sessions",
      body: "Every chat session with its turns, tokens and cost. Arriving from All Users adds an IP filter you can clear in the toolbar; click a session to list its turns.",
      placement: "top",
      tab: "sessions",
    },
    {
      target: t("queries-stats"),
      title: "All Queries",
      body: "Turn-level stats: total queries, groundedness pass rate, average latency (a mean, in ms) and average billed tokens per turn. The outcome pills below filter the table by answered, ungrounded, blocked and so on.",
      placement: "bottom",
      tab: "queries",
    },
    {
      target: t("queries-table"),
      title: "Turns and the inspector",
      body: "Search by question, answer, IP or request ID. Click any row to open the Single-Query Economic Inspector: the token budget split, the per-stage cost lines and a link to the full governance trace.",
      placement: "top",
    },
    {
      target: t("queries-export"),
      title: "Export CSV",
      body: "Downloads every turn matching the current filters (up to 10,000 rows, newest first). Each table tab has its own export; it needs admin rights.",
      placement: "left",
    },
    {
      title: "That's Usage & Cost",
      body: "Start from net spend and the forecast, then drill from user, to session, to turn to explain any spike. Use Configure Test IPs to keep QA traffic out of production numbers. Related: Conversations, Governance traces and the Dashboard.",
      placement: "center",
    },
  ],
};
