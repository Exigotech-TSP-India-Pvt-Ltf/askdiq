import type { PageTour } from "../types";

const kpi = (n: number) => `[data-tour="feedback-kpis"] > :nth-child(${n})`;
const mainChart = (n: number) => `[data-tour="feedback-charts-main"] > :nth-child(${n})`;
const breakdown = (n: number) => `[data-tour="feedback-charts-breakdown"] > :nth-child(${n})`;

export const feedbackTour: PageTour = {
  id: "feedback",
  title: "Feedback",
  steps: [
    {
      title: "The human feedback loop",
      body: "This page shows how people rate the assistant's answers and turns those ratings into fixes: triage them, promote failing turns to golden test cases, and export training pairs. Operators read it; admins triage and export. The audience switch in the header scopes Overview, Review Queue and Knowledge Gaps.",
      placement: "center",
    },
    {
      target: '[data-tour="feedback-tabs"]',
      title: "Five sections",
      body: "Overview for the charts, Review Queue to triage, Golden Cases for promoted regressions, Preference Data for export, Knowledge Gaps for missing content. Badges show new ratings, promoted ratings and detected gaps.",
      placement: "bottom",
      tab: "overview",
    },
    {
      target: '[data-tour="feedback-kpis"]',
      title: "Headline numbers",
      body: "Total feedback counts every rating for this audience. Positive is the share of 4-5 star ratings; Needs improvement is the share of 1-2 stars. Click a day on any sparkline to open the queue filtered to that day.",
      placement: "bottom",
    },
    {
      target: kpi(3),
      title: "Needs improvement and the spike banner",
      body: "When more than 25% of ratings are 1-2 stars (with at least 5 ratings), this card turns red and a spike banner appears above the cards. Its \"Review negative feedback\" button opens the queue filtered to negative ratings.",
      placement: "bottom",
    },
    {
      target: kpi(4),
      title: "Coverage",
      body: "The share of assistant answers that received a rating. Low coverage means the other numbers rest on a small sample, so read them with care.",
      placement: "left",
    },
    {
      target: mainChart(1),
      title: "Satisfaction trend",
      body: "Positive and negative ratings per day. A growing negative band is the early warning. Click a day to triage exactly those ratings; the table icon shows the raw numbers.",
      placement: "bottom",
    },
    {
      target: mainChart(2),
      title: "Rating distribution",
      body: "How many ratings landed on each star value: green for 4-5, amber for 3, red for 1-2. Click a bar to filter the queue by that rating.",
      placement: "left",
    },
    {
      target: breakdown(1),
      title: "Feedback by outcome",
      body: "What the pipeline did on the rated answers: answered, ungrounded, blocked and so on. If one outcome dominates, look at that path first. Click a bar to filter.",
      placement: "top",
    },
    {
      target: breakdown(2),
      title: "Top reasons",
      body: "The reason chips people picked on a thumbs-down. Click a reason to see every rating that used it.",
      placement: "top",
    },
    {
      target: breakdown(3),
      title: "Triage funnel",
      body: "How far ratings have moved: new, reviewed, promoted to a golden case. Each stage counts everything that reached it, so a big drop after New means a triage backlog.",
      placement: "top",
    },
    {
      target: breakdown(4),
      title: "Human vs groundedness",
      body: "Puts the human rating against the pipeline's own groundedness check. The disagreement corners matter most: liked but ungrounded, or disliked but grounded. Click a quadrant to review those ratings.",
      placement: "left",
    },
    {
      target: '[data-tour="feedback-queue"]',
      title: "Review queue",
      body: "Every rating with its question, answer, outcome and status. Click a row (or press Enter) to open the full turn in a drawer, where you can add a note or a corrected answer. Use Next and Previous at the bottom to page.",
      placement: "top",
      tab: "queue",
    },
    {
      target: '[data-tour="feedback-queue-toolbar"]',
      title: "Filter and triage fast",
      body: "Search by question, comment, user or ID, and filter by status, rating, outcome, grounding and comment. With triage rights, use j / k to move and r / d / p to mark reviewed, dismiss or promote; tick rows for bulk actions.",
      placement: "bottom",
    },
    {
      target: '[data-tour="feedback-golden"]',
      title: "Golden cases",
      body: "Answers promoted from feedback into the regression suite, with their last evaluation result. Use \"Run now\" to re-test a case, or the Evaluation (Live) link for the full scorecard. This list is not audience-scoped.",
      placement: "top",
      tab: "golden",
    },
    {
      target: '[data-tour="feedback-pairs"]',
      title: "Preference data",
      body: "Chosen vs rejected answer pairs for a later DPO or SFT training run, PII-masked. A pair is complete once someone saves a corrected answer. Tick \"Complete pairs only\" and use Download JSONL to export (needs the feedback:export permission).",
      placement: "top",
      tab: "pairs",
    },
    {
      target: '[data-tour="feedback-panel"]',
      title: "Knowledge gaps",
      body: "Questions that got low ratings and weak retrieval, which usually means the answer is missing from the documents. Use \"Show in queue\" to read the turn, or \"Open document\" / \"Add document\" to fix the source.",
      placement: "top",
      tab: "gaps",
    },
    {
      title: "Close the loop",
      body: "Watch the trend, triage the low ratings, promote failures to golden cases and fill the gaps in Documents. Then check Evaluation (Live) to confirm quality recovers, and Conversation Logs for full sessions.",
      placement: "center",
      tab: "overview",
    },
  ],
};
