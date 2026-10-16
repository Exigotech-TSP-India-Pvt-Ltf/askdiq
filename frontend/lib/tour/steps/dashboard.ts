import type { PageTour } from "../types";

export const dashboardTour: PageTour = {
  id: "dashboard",
  title: "Dashboard",
  steps: [
    {
      title: "Welcome to the Dashboard",
      body: "This page answers one question: how is the assistant doing right now, compared with the period before? Admins and analysts use it to spot trends, and every card and chart opens the page that owns the number, already filtered.",
      placement: "center",
    },
    {
      target: '[data-tour="dashboard-header"]',
      title: "Audience and date range",
      body: "Overview counts everything, Users excludes test IPs, and Testing shows only test or unknown IPs. The date range (default Last 7 days) drives every KPI and most charts, and it carries over when you click through. Try switching to Users to see real production traffic only.",
      placement: "bottom",
    },
    {
      target: '[data-tour="dashboard-health"]',
      title: "Health pill",
      body: "Healthy, Degraded or Incident: the worst of error rate, p95 latency and Azure quota use over the last hour. Click it to open Governance > Overall for the details.",
      placement: "bottom",
    },
    {
      target: '[data-tour="dashboard-kpis"]',
      title: "KPI cards",
      body: "Eight headline numbers for the selected range: users, active users, sessions, queries, feedback, average latency, error rate and cost. The badge compares with the previous period of equal length (green means a good direction), and the sparkline shows the trend. Click any card to open its page.",
      placement: "bottom",
    },
    {
      target: '[data-tour="dashboard-active-visitors"]',
      title: "Active visitors: now / DAU / WAU / MAU",
      body: "Active visitors are distinct end users with at least one query in the period, identified by client id (or by IP when there is none), with test IPs excluded. Now covers the last few minutes; DAU, WAU and MAU cover the last 1, 7 and 30 days. Always as of now, independent of the date range, with a trend against the previous equal window.",
      placement: "bottom",
    },
    {
      target: '[data-tour="dashboard-outcomes"]',
      title: "Queries by outcome",
      body: "Answered, ungrounded, blocked, conversational and LLM-failed exchanges over time. A growing ungrounded or blocked band is the first sign that content or guardrails need attention. Click a day to open those queries in Usage.",
      placement: "top",
    },
    {
      target: '[data-tour="dashboard-satisfaction"]',
      title: "Satisfaction",
      body: "The thumbs-up share of rated answers: ratings 4-5 count as up, 1-2 as down, and 3s are ignored. It turns amber below 70% and red below 50%. Click to open Feedback.",
      placement: "left",
    },
    {
      target: '[data-tour="dashboard-latency"]',
      title: "Latency p50 / p95",
      body: "End-to-end response time per bucket: p50 is the typical answer, p95 the slow tail. A rising p95 with a flat p50 usually means a few heavy queries. Click to open Monitoring.",
      placement: "top",
    },
    {
      target: '[data-tour="dashboard-cost"]',
      title: "Cost vs budget",
      body: "Month-to-date spend, a straight-line month-end forecast and the configured monthly budget. This widget always shows the current UTC month and ignores the date range. Click to open Usage & Cost.",
      placement: "left",
    },
    {
      target: '[data-tour="dashboard-intents"]',
      title: "Top intents",
      body: "The eight most common question types, sized by share of classified turns. Classification runs in the background, so the newest turns can be missing. Click an intent to explore it in Analytics.",
      placement: "top",
    },
    {
      target: '[data-tour="dashboard-activity"]',
      title: "Live activity",
      body: "The newest 20 events (queries, errors, guardrail blocks, feedback, document and admin actions), refreshed every 15 seconds and independent of the date range. Click a row to jump to its trace, feedback or conversation.",
      placement: "top",
    },
    {
      target: '[data-tour="dashboard-pipeline"]',
      title: "RAG pipeline",
      body: "Average time per stage (embedding, retrieval, reranker and LLM synthesis) over the last 24 hours, across all traffic. The bars show which stage dominates the response time.",
      placement: "left",
    },
    {
      title: "That's the Dashboard",
      body: "Start here, spot what changed, then click through: Visitors for who is using it, Usage & Cost for volume and spend, Feedback for ratings, and Governance for health and traces.",
      placement: "center",
    },
  ],
};
