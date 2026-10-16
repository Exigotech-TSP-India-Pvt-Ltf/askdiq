import type { PageTour } from "../types";

const LATENCY = '[data-tour="governance-mon-latency"]';

export const governanceTour: PageTour = {
  id: "governance",
  title: "Governance",
  steps: [
    {
      title: "Welcome to Governance",
      body: "Governance tells you whether the assistant is healthy and helps you find out why a single answer was slow, blocked or failed. Operators watch Overall for incidents; engineers use Monitoring, Traces and Logs to investigate.",
      placement: "center",
    },
    {
      target: '[data-tour="governance-tabs"]',
      title: "Four views",
      body: "Overall is the health summary, Monitoring holds live latency and traffic charts, Traces lists every request and Logs holds audit and security events. The header date range applies to Traces and Logs.",
      placement: "bottom",
    },

    // Overall
    {
      tab: "Overall",
      target: '[data-tour="governance-health"]',
      title: "Health banner",
      body: "Checks error rate, p95 latency and Azure quota over the last 60 minutes. Healthy: all under threshold. Degraded: one passed its warning level (default 2% errors, 8 s p95, 80% quota). Incident: one passed its incident level (10%, 20 s, 95%).",
      placement: "bottom",
    },
    {
      target: '[data-tour="governance-kpis"]',
      title: "KPI cards",
      body: "Eight 24-hour figures, such as requests, error rate, p50 and p95 latency, groundedness and blocked or flagged turns, each with an hourly sparkline. Click a card to open the rows behind it.",
      placement: "bottom",
    },
    {
      target: '[data-tour="governance-timeline"]',
      title: "Status timeline",
      body: "One cell per hour coloured by health: green healthy, amber degraded, red incident, grey no traffic. Red markers count incidents; select one to open Logs for that hour.",
      placement: "top",
    },
    {
      target: '[data-tour="governance-incidents"]',
      title: "Recent incidents",
      body: "CRITICAL and ALERT log entries from the last 24 hours, newest first. New ones arrive live and briefly highlight.",
      placement: "top",
    },

    // Monitoring
    {
      tab: "Monitoring",
      target: '[data-tour="governance-mon-tiles"]',
      title: "Live stat tiles",
      body: "Requests, queries, active visitors, traces, error rate, average and p95 latency and in-flight queries, each labelled with its window and source. Every tile links to the matching traces or logs.",
      placement: "bottom",
    },
    {
      target: `${LATENCY} > :nth-child(1)`,
      title: "Pipeline latency",
      body: "Average time each RAG stage takes, such as retrieval and the LLM call. The tallest bar is where requests spend most of their time.",
      placement: "right",
    },
    {
      target: `${LATENCY} > :nth-child(2)`,
      title: "Stage percentiles",
      body: "For each stage the band runs from p50 (a typical request) to p95 (a slow one), and the dashed line marks p99. A wide band means that stage is inconsistent.",
      placement: "left",
    },
    {
      target: `${LATENCY} > :nth-child(4)`,
      title: "Requests and errors",
      body: "Hourly successful assistant turns stacked with server errors in red. Compare it with Latency over time beside it: a red spike that lines up with slow hours usually points at the model provider.",
      placement: "left",
    },
    {
      target: '[data-tour="governance-mon-resources"]',
      title: "CPU, RAM and Azure quota",
      body: "Gauges for this API process's CPU and RAM and the share of Azure tokens-per-minute and requests-per-minute in use. They turn amber at 70–75% and red at 90%.",
      placement: "top",
    },
    {
      target: '[data-tour="governance-mon-guardrails"]',
      title: "Guardrail flags and outcome mix",
      body: "Left: how often each guardrail flag fired on assistant turns in 24 hours (a turn with two flags counts twice). Right: turns split by outcome, so you can tell a handful of failures from a trend.",
      placement: "top",
    },

    // Traces
    {
      tab: "Traces",
      target: '[data-tour="governance-traces-filters"]',
      title: "Find a request",
      body: "Search by query text or request ID and filter by status, outcome and latency bucket: Fast is under 1 s, Moderate 1–3 s, Slow 3–10 s, Very slow 10 s or more. Try Latency → Slow.",
      placement: "bottom",
    },
    {
      target: '[data-tour="governance-traces-table"] tbody',
      title: "Trace waterfall",
      body: "Each row is one request with its status, latency, slowest stage, tokens and cost. Click a row to open the drawer with the stage-by-stage waterfall. Auto-refresh (top right) reloads the list every 15 s.",
      placement: "top",
    },

    // Logs
    {
      tab: "Logs",
      target: '[data-tour="governance-logs-levels"]',
      title: "Level chips",
      body: "Toggle levels such as INFO, WARNING, ALERT and CRITICAL; each chip shows how many entries match. CRITICAL and ALERT rows are the incidents shown on Overall.",
      placement: "bottom",
    },
    {
      target: '[data-tour="governance-logs-search"]',
      title: "Search and filters",
      body: "Search summary, action or request ID, and narrow by service. Clear all resets the level chips and any hour filter opened from the status timeline.",
      placement: "bottom",
    },
    {
      target: '[data-tour="governance-logs-autorefresh"]',
      title: "Auto-refresh and Export CSV",
      body: "Auto-refresh reloads the first page every 15 seconds, pausing while the browser tab is hidden. Export CSV next to it downloads every entry matching your current filters.",
      placement: "left",
    },
    {
      target: '[data-tour="governance-logs-table"] nav',
      title: "Pagination",
      body: "Page through results and change the page size. Request IDs in the table link straight to their trace.",
      placement: "top",
    },
    {
      title: "That's Governance",
      body: "Start on Overall, follow a red marker or KPI card into Traces or Logs, then open the waterfall to find the slow stage. For what people ask and how well it was answered, see Analytics; for tokens and cost, see Usage.",
      placement: "center",
    },
  ],
};
