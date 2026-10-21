import type { PageTour } from "../types";

export const evaluationTour: PageTour = {
  id: "evaluation",
  title: "Evaluation",
  steps: [
    {
      title: "Answer-quality scorecard",
      body: "This page scores how well the assistant answers: pass rate, retrieval, groundedness, citation quality and safety. Admins and ML owners use it to check that quality holds up. It is read-only.",
      placement: "center",
    },
    {
      target: '[data-tour="evaluation-header"]',
      title: "Benchmark or live traffic",
      body: "The audience switch picks the data source. Overview and Testing show the offline benchmark suite (golden test cases); Users shows live scoring of the latest real production answers. So the default view is the benchmark, despite the \"Live\" title.",
      placement: "bottom",
    },
    {
      target: '[data-tour="evaluation-compare"]',
      title: "Compare with benchmark",
      body: "Shows a comparison matrix above the tabs: pass rate, faithfulness, recall and citation precision for the benchmark vs live users, each with a delta, plus an overlaid radar. Click Compare with benchmark on the tab row again to hide it.",
      placement: "left",
    },
    {
      target: '[data-tour="evaluation-tabs"]',
      title: "Four evaluation domains",
      body: "Quality & Retrieval, Semantic Quality & Citations, Safety & Robustness, and the Query & Case Explorer for single cases. The selected tab is kept in the URL, so you can share a link.",
      placement: "bottom",
      tab: "overview",
    },
    {
      target: '[data-tour="evaluation-overview-kpis"]',
      title: "Quality headline",
      body: "Pass rate is the share of cases judged correct. Retrieval recall, MRR and NDCG@k show whether the right source was found and ranked near the top; groundedness is the share of answers backed by the sources.",
      placement: "bottom",
    },
    {
      target: '[data-tour="evaluation-overview-radar"]',
      title: "Quality radar and case outcomes",
      body: "The radar shows every quality dimension at once, so a dent points to the weak area. The donut splits cases into pass and fail.",
      placement: "top",
    },
    {
      target: '[data-tour="evaluation-overview-trend"]',
      title: "Pass-rate trend",
      body: "Pass rate per run over time: one benchmark report, or one UTC day of live scoring for Users. Click a run to select it; a banner notes that the figures below still show the latest evaluation.",
      placement: "top",
    },
    {
      target: '[data-tour="evaluation-overview-breakdown"]',
      title: "By query type and latency",
      body: "Pass rate per query type (per turn outcome for Users) shows which kinds of question fail. Latency shows p50, p95 and p99 per pipeline stage, so you can spot the slow step.",
      placement: "top",
    },
    {
      target: '[data-tour="evaluation-overview-retrieval"]',
      title: "Retrieval ranking metrics",
      body: "Recall @1, @3 and @k: how often the right source is in the top results. Precision and context coverage show how much retrieved context is useful; noise ratio is the irrelevant share.",
      placement: "top",
    },
    {
      target: '[data-tour="evaluation-judge-kpis"]',
      title: "LLM judge scores",
      body: "An LLM judge rates answers from 1 to 5 for faithfulness (sticks to the sources), relevance (answers the question) and completeness.",
      placement: "bottom",
      tab: "judge",
    },
    {
      target: '[data-tour="evaluation-judge-citations"]',
      title: "Citation verification",
      body: "Citation precision is the share of cited claims that the cited passage supports; coverage is the share of claims that carry a citation. Low precision means sources are attached but do not back the answer.",
      placement: "top",
    },
    {
      target: '[data-tour="evaluation-safety-kpis"]',
      title: "Safety scorecard",
      body: "Safety passed is the share of cases with no safety failure. PII leakage and secret exposure rates should be zero; injection compliance tracks prompt-injection test cases.",
      placement: "bottom",
      tab: "safety",
    },
    {
      target: '[data-tour="evaluation-safety-robustness"]',
      title: "Robustness and abstention",
      body: "Robustness re-asks questions with typos, paraphrases and adversarial changes and checks that retrieval, answers and citations stay stable. Below it, abstention shows how often the assistant declines unanswerable questions, and how often it declines wrongly.",
      placement: "top",
    },
    {
      target: '[data-tour="evaluation-explorer"]',
      title: "Query & Case Explorer",
      body: "Every evaluated case: pass or fail, type, query, confidence, recall, citation precision, faithfulness and latency. Search queries, answers or IDs, and filter by evaluation status or grounding.",
      placement: "top",
      tab: "explorer",
    },
    {
      target: '[data-tour="evaluation-explorer-table"]',
      title: "Inspect a case",
      body: "Click a row or its Inspect button to open the query inspector drawer with the full answer, sources and scores. Use the pagination below the table to change page or page size.",
      placement: "top",
    },
    {
      title: "Benchmark first, then live",
      body: "Use the benchmark (Overview / Testing) to check a release and Users to see real-world quality; Compare shows the gap. Low-rated live answers can be promoted to golden cases from the Feedback page.",
      placement: "center",
      tab: "overview",
    },
  ],
};
