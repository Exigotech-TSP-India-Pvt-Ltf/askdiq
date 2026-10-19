/** Responses shaped exactly like docs/technical/frontend/contracts/entity-stats.md (typed against lib/entity-stats-api.ts). */
import type {
  ConversationDetail,
  ConversationRow,
  ConversationStats,
  DocumentChunk,
  DocumentCitationStats,
  LowRatedCitations,
  VisitorDetail,
  VisitorRow,
  VisitorStats,
  VisitorTimeline,
} from "@/lib/entity-stats-api";
import type { DocumentSummary } from "@/lib/types";

export const IP = "203.0.113.7";
export const VISITOR_ID = `client-${IP}`;
export const CONV_ID = "6f1c0000-0000-4000-8000-000000000001";
export const CONV_ID_2 = "6f1c0000-0000-4000-8000-000000000002";
export const DOC_ID = "d0c00000-0000-4000-8000-000000000001";
export const CHUNK_ID = "c4a00000-0000-4000-8000-000000000003";
export const ASSISTANT_MSG = "a5500000-0000-4000-8000-000000000011";

export const visitorRows: VisitorRow[] = [
  {
    id: VISITOR_ID,
    ip_address: IP,
    is_test: false,
    type: "Production",
    status: "Active",
    sessions: 3,
    conversations: 3,
    queries: 9,
    feedback_count: 1,
    first_seen: "2026-10-01T00:00:00Z",
    last_active: "2026-10-14T09:31:02Z",
  },
  {
    id: "client-198.51.100.2",
    ip_address: "198.51.100.2",
    is_test: true,
    type: "Test",
    status: "Active",
    sessions: 1,
    queries: 2,
    feedback_count: 0,
    first_seen: "2026-10-02T00:00:00Z",
    last_active: "2026-10-12T10:00:00Z",
  },
];

export const visitorStats: VisitorStats = {
  from: "2026-10-09",
  to: "2026-10-15",
  tz: "UTC",
  kpis: {
    visitors: 120,
    new_visitors: 30,
    returning_visitors: 90,
    active_now: 4,
    sessions: 340,
    queries: 1200,
    avg_sessions_per_visitor: 2.83,
  },
  new_vs_returning: [
    { day: "2026-10-09", new: 4, returning: 12 },
    { day: "2026-10-10", new: 6, returning: 10 },
  ],
  heatmap: [
    { weekday: 0, hour: 9, sessions: 12 },
    { weekday: 2, hour: 14, sessions: 5 },
  ],
};

export const visitorDetail: VisitorDetail = {
  ...visitorRows[0],
  conversations_data: [
    {
      id: CONV_ID,
      title: "Leave policy",
      created_at: "2026-10-14T09:12:44Z",
      last_activity_at: "2026-10-14T09:31:02Z",
      message_count: 4,
    },
  ],
};

export const visitorTimeline: VisitorTimeline = {
  visitor_id: VISITOR_ID,
  ip_address: IP,
  sessions: [
    {
      session_id: CONV_ID,
      title: "Leave policy",
      started_at: "2026-10-14T09:12:44Z",
      ended_at: "2026-10-14T09:31:02Z",
      turns: 2,
      cost_usd: 0.0123,
    },
  ],
  queries: [
    {
      request_id: "req_ab12",
      session_id: CONV_ID,
      turn_index: 0,
      query: "How many leave days do I get?",
      outcome: "answered",
      grounded: true,
      created_at: "2026-10-14T09:12:44Z",
      latency_ms: 1840.5,
      cost_usd: 0.0031,
    },
  ],
  cost_series: [
    { day: "2026-10-13", cost_usd: 0.002, queries: 1 },
    { day: "2026-10-14", cost_usd: 0.0123, queries: 4 },
  ],
  feedback: [
    {
      id: "fb_1",
      message_id: ASSISTANT_MSG,
      session_id: CONV_ID,
      turn_index: 0,
      request_id: "req_ab12",
      rating: 2,
      comment: "Out of date",
      reason_tags: ["wrong"],
      created_at: "2026-10-14T09:13:10Z",
    },
  ],
  truncated: { sessions: false, queries: false, feedback: false },
};

export const conversationRows: ConversationRow[] = [
  {
    id: CONV_ID,
    title: "Leave policy",
    created_ip: IP,
    message_count: 4,
    turn_count: 2,
    created_at: "2026-10-14T09:12:44Z",
    last_activity_at: "2026-10-14T09:31:02Z",
  },
  {
    id: CONV_ID_2,
    title: "Expenses",
    created_ip: IP,
    message_count: 2,
    created_at: "2026-10-13T09:12:44Z",
    last_activity_at: "2026-10-13T09:31:02Z",
  },
];

export const conversationStats: ConversationStats = {
  from: "2026-10-09",
  to: "2026-10-15",
  totals: { conversations: 290, turns: 1100, avg_turns: 3.79 },
  turns_histogram: [
    { min_turns: 1, max_turns: 1, conversations: 80 },
    { min_turns: 2, max_turns: 3, conversations: 120 },
    { min_turns: 10, max_turns: null, conversations: 10 },
  ],
  outcome_by_day: [
    { day: "2026-10-09", counts: { answered: 30, ungrounded: 2, blocked: 1, conversational: 5, llm_failed: 0 } },
    { day: "2026-10-10", counts: { answered: 20, ungrounded: 0, blocked: 3, conversational: 1, llm_failed: 1 } },
  ],
};

export const conversationDetailBase: ConversationDetail = {
  conversation: {
    id: CONV_ID,
    title: "Leave policy",
    created_ip: IP,
    created_at: "2026-10-14T09:12:44Z",
    last_activity_at: "2026-10-14T09:31:02Z",
  },
  messages: [
    { id: "u0", turn_index: 0, role: "user", content: "How many leave days do I get?" },
    { id: "a5500000-0000-4000-8000-000000000010", turn_index: 0, role: "assistant", content: "You get 20 days.", request_id: "req_0", grounded: true },
    { id: "u1", turn_index: 1, role: "user", content: "And sick leave?" },
    { id: ASSISTANT_MSG, turn_index: 1, role: "assistant", content: "10 days of sick leave.", request_id: "req_ab12", grounded: true },
  ],
  message_count: 4,
};

export const conversationDetail: ConversationDetail = {
  ...conversationDetailBase,
  focus_turn: 1,
  turns: [0, 1].map((i) => ({
    turn_index: i,
    request_id: i === 0 ? "req_0" : "req_ab12",
    user_message_id: `u${i}`,
    assistant_message_id: i === 0 ? "a5500000-0000-4000-8000-000000000010" : ASSISTANT_MSG,
    created_at: "2026-10-14T09:14:02Z",
    outcome: "answered",
    grounded: true,
    stages_ms: { guardrail_pre: 12.4, embedding: 41, retrieval: 80.2, reranker: null, llm: 1650.7, guardrail_post: 6.1 },
    total_ms: 1790.4,
    tokens: { prompt: 2400, cached_prompt: 1024, completion: 380, reasoning: 128, embedding: 12, guardrail: 8, total: 2800 },
    cost_usd: 0.0031,
    sources: [
      { document_id: DOC_ID, chunk_id: CHUNK_ID, chunk_index: 3, source_name: "Leave policy.md", section_heading: "Annual leave", score: 0.82 },
    ],
    feedback:
      i === 1
        ? [{ id: "fb_1", rating: 2, comment: "Out of date", reason_tags: ["wrong"], triage_status: "new", created_at: "2026-10-14T09:15:00Z" }]
        : [],
    labels: { intent: "leave", quality: "partial", issue: null },
    promoted_case_id: null,
  })),
};

export const documents: DocumentSummary[] = [
  {
    document_id: DOC_ID,
    source_name: "Leave policy.md",
    source_type: "markdown",
    chunk_count: 2,
    created_at: "2026-04-01T00:00:00Z",
    updated_at: "2026-04-01T00:00:00Z",
    converted_from: "md",
    original_filename: "Leave policy.md",
    content_hash: "abc123",
    original_sha256: "abc123",
    ingest_count: 1,
    warnings: [],
    quarantined: false,
  } as DocumentSummary,
];

export const citationStats: DocumentCitationStats = {
  from: "2026-09-16",
  to: "2026-10-15",
  top_cited: [{ document_id: DOC_ID, source_name: "Leave policy.md", citations: 120, last_cited_at: "2026-10-15T08:00:00Z" }],
  age_vs_citations: [{ document_id: DOC_ID, source_name: "Leave policy.md", age_days: 200, citations: 3 }],
  coverage: { answered_queries: 1000, cited_queries: 870, coverage_pct: 87 },
};

export const chunks: DocumentChunk[] = [
  {
    id: "c4a00000-0000-4000-8000-000000000001",
    chunk_index: 0,
    content: "Introduction to the policy.",
    strategy: "structure_aware",
    section_heading: "Intro",
    char_count: 27,
    hits: 2,
    weekly_hits: [{ week_start: "2026-10-05", hits: 2 }],
  },
  {
    id: CHUNK_ID,
    chunk_index: 3,
    content: "Annual leave is 20 days per year.",
    strategy: "structure_aware",
    section_heading: "Annual leave",
    char_count: 33,
    hits: 42,
    low_rated_hits: 3,
    weekly_hits: [
      { week_start: "2026-10-05", hits: 30 },
      { week_start: "2026-10-12", hits: 12 },
    ],
  },
];

export const lowRated: LowRatedCitations = {
  document_id: DOC_ID,
  total: 1,
  items: [
    {
      message_id: ASSISTANT_MSG,
      session_id: CONV_ID,
      turn_index: 1,
      request_id: "req_ab12",
      chunk_id: CHUNK_ID,
      chunk_index: 3,
      query: "And sick leave?",
      rating: 2,
      reason_tags: ["wrong"],
      comment: "Out of date",
      created_at: "2026-10-14T09:15:00Z",
    },
  ],
};
