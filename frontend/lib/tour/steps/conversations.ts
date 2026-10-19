import type { PageTour } from "../types";

const t = (id: string): string => `[data-tour="conversations-${id}"]`;

// One tour serves the list (/admin/conversations) and the detail page
// (/admin/conversations/<id>); steps whose target is not on screen are the
// detail-page steps and only spotlight there.
export const conversationsTour: PageTour = {
  id: "conversations",
  title: "Conversations",
  steps: [
    {
      title: "Conversation logs",
      body: "The Conversations tab of Visitors & Conversations: every chat session the assistant handled, with its turns, outcomes and cost. Support uses it to read what a visitor actually asked; analysts use it to spot long or failing conversations.",
      placement: "center",
    },
    {
      target: '[data-tour="audience-tabs"]',
      title: "Visitors and Conversations tabs",
      body: "Switch back to Visitors to see who asked. Both tabs share the audience and date range.",
      placement: "bottom",
    },
    {
      target: t("header"),
      title: "Audience and date range",
      body: "The audience tabs and the date-range picker scope the cards, charts and table. Switch to Users to hide test traffic.",
      placement: "bottom",
    },
    {
      target: t("stats"),
      title: "Headline numbers",
      body: "Conversations in the range, average turns per conversation (one turn is a question plus its answer) and total turns. They follow search and IP filters but ignore the chart filters.",
      placement: "bottom",
    },
    {
      target: `${t("charts")} > :nth-child(1)`,
      title: "Conversation length",
      body: "How many conversations had 1, 2, 3… turns. Click a bar to list only conversations of that length.",
      placement: "bottom",
    },
    {
      target: `${t("charts")} > :nth-child(2)`,
      title: "Outcomes by day",
      body: "Each day's turns split by outcome: answered (green), ungrounded (amber), blocked or failed (red), conversational. Click a segment, for example a spike of ungrounded, to list the conversations behind it.",
      placement: "bottom",
    },
    {
      target: t("filters"),
      title: "Search and filters",
      body: "Search by title, ID or origin IP, or pick an origin IP. Active filters appear as chips; click the X to clear one, or use the browser Back button to undo.",
      placement: "bottom",
    },
    {
      target: t("table"),
      title: "Conversation table",
      body: "One row per conversation with origin IP (shown as Not recorded when a server-to-server call sent no client IP), turns, messages and activity times; sort by Created or Last Active. Click a row or the eye icon to preview it in a side drawer, then Open full view for the detail page.",
      placement: "top",
    },
    {
      target: t("detail-stats"),
      title: "Conversation detail",
      body: "On a conversation's page: the visitor, when it started, the number of turns and the total cost and tokens.",
      placement: "bottom",
    },
    {
      target: `${t("detail-charts")} > :nth-child(1)`,
      title: "Per-turn timings",
      body: "Stage latency of the selected turn: how long retrieval, generation and the other stages took. Click a stage to open the query inspector.",
      placement: "bottom",
    },
    {
      target: `${t("detail-charts")} > :nth-child(2)`,
      title: "Tokens per turn",
      body: "Billed tokens for each turn, so the expensive turn stands out. Click a bar to jump to that turn.",
      placement: "bottom",
    },
    {
      target: t("detail-turns"),
      title: "Turns",
      body: "Each card shows the question, the answer, its outcome, latency and cost. Inspect opens the cost breakdown, Trace opens Governance, and Promote to test turns it into an evaluation case.",
      placement: "top",
    },
    {
      target: t("detail-turn-footer"),
      title: "Feedback, labels and sources",
      body: "Visitor ratings and comments, the insight labels (intent, quality, issue) and the documents the answer cited. Follow Open in Feedback to triage a complaint.",
      placement: "top",
    },
    {
      title: "That's Conversation logs",
      body: "Use the charts to find a pattern, the table to find the conversation, and the detail page to see exactly what happened in each turn. Related: Usage & Cost, Feedback and Governance.",
      placement: "center",
    },
  ],
};
