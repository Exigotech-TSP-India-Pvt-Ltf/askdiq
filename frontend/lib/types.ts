// Mirrors app/schemas/* on the backend. Keep these in sync with the API.

export interface UserOut {
  id: string;
  full_name: string;
  email: string;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  user: UserOut;
}

export interface SourceChunk {
  chunk_id: string;
  document_id: string;
  content: string;
  score: number;
  metadata: Record<string, unknown>;
}

export interface QueryResponse {
  answer: string;
  sources: SourceChunk[];
  grounded: boolean;
  confidence: number;
  request_id: string;
  latency_ms: number;
  guardrail_flags: string[];
  session_id: string;
  session_title: string;
  user_message_id: string;
  assistant_message_id: string;
}

export interface ChatSessionSummary {
  id: string;
  title: string;
  created_at: string;
  last_activity_at: string;
  expires_at: string;
  is_expired: boolean;
  message_count: number;
}

export interface ChatMessageOut {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  request_id?: string | null;
  grounded?: boolean | null;
  guardrail_flags?: string[] | null;
  feedback_rating?: number | null;
}

export interface ChatSessionDetail extends ChatSessionSummary {
  messages: ChatMessageOut[];
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt: number;
  // Present on assistant messages once the /query response resolves.
  response?: QueryResponse;
  pending?: boolean;
  error?: string;
  feedbackRating?: number | null;
  /** Saved answers' request id (the live `response` carries its own). */
  requestId?: string | null;
}

export interface StageTokenBreakdown {
  query_embed: number;
  prompt: number;
  reasoning: number;
  visible: number;
  guardrail_embed: number;
  total_billed: number;
}

export interface StageCostBreakdown {
  query_embed: number;
  prompt: number;
  reasoning: number;
  visible: number;
  guardrail_embed: number;
  total_usd: number;
}

export interface FeedbackSubmission {
  message_id: string;
  rating: number; // 1-5
  comment?: string | null;
  conversation_id: string;
}

export interface IngestResult {
  source_name: string;
  original_filename?: string;
  converted_from?: string;
  status: "new" | "updated" | "unchanged" | "repaired" | "forced";
  content_hash: string;
  previous_content_hash?: string | null;
  chunks_added: number;
  chunks_kept: number;
  chunks_removed: number;
  embedding_tokens: number;
  embedding_cost_usd: number;
  warnings: string[];
  quarantined: boolean;
  images_dropped?: number;
}

export type Audience = "overview" | "users" | "testing";

export interface PageEnvelope<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
  stats?: Record<string, any>;
}

export interface AudienceCounts {
  overview: { queries: number; sessions: number; ips: number };
  users: { queries: number; sessions: number; ips: number };
  testing: { queries: number; sessions: number; ips: number };
}

export interface UsageSummary {
  audience: Audience;
  as_of: string;
  financial: {
    total_cost_usd: number;
    llm_cost_usd: number;
    embeddings_cost_usd: number;
    avg_cost_per_query_usd: number;
    stage_costs: {
      query_embed_cost: number;
      guardrail_embed_cost: number;
      prompt_uncached_cost: number;
      cached_prompt_cost: number;
      cached_savings_usd: number;
      reasoning_cost: number;
      visible_cost: number;
      ingestion_overhead_cost: number;
    };
  };
  tokens: {
    total_billed_tokens: number;
    llm_tokens: {
      prompt_context_tokens: number;
      cached_prompt_tokens: number;
      reasoning_tokens: number;
      visible_output_tokens: number;
      total_llm_tokens: number;
    };
    embeddings_tokens: {
      query_embed_tokens: number;
      guardrail_embed_tokens: number;
      ingestion_embed_tokens: number;
      total_embeddings_tokens: number;
    };
  };
  pricing: {
    price_embed_per_million: number;
    price_prompt_per_million: number;
    price_output_per_million: number;
    price_cached_per_million: number;
  };
  accounting: {
    total_turns: number;
    actual_turns: number;
    estimated_turns: number;
    actual_pct: number;
  };
  quality: {
    total_queries: number;
    outcomes: Record<string, number>;
    groundedness_pass_rate: number;
    avg_billed_tokens_per_turn: number;
    latency: {
      avg_ms: number;
      p50_ms: number;
      p95_ms: number;
    };
  };
}

export interface UsageUserRow {
  ip_address: string;
  is_test: boolean;
  type: string;
  sessions: number;
  queries: number;
  embed_tokens: number;
  prompt_tokens: number;
  reasoning_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost_usd: number;
}

export interface UsageSessionRow {
  id: string;
  title: string;
  ip_address: string;
  queries: number;
  embed_tokens: number;
  prompt_tokens: number;
  reasoning_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost_usd: number;
  last_active: string | null;
}

export interface UsageQueryRow {
  request_id: string;
  created_at: string | null;
  query: string;
  answer: string;
  ip_address: string;
  session_id: string;
  session_title: string;
  prompt_tokens: number;
  reasoning_tokens: number;
  output_tokens: number;
  embed_tokens: number;
  cost_usd: number;
  outcome: string;
  grounded: boolean | null;
  latency_ms: number | null;
}

export interface QueryInspectorDetail {
  request_id: string;
  timestamp: string | null;
  outcome: string;
  grounded: boolean | null;
  cost_usd: number;
  pricing_rate_card: Record<string, number>;
  session: { id: string; title: string; ip: string };
  user_query: string;
  assistant_answer: string;
  latency: {
    total_ms: number | null;
    retrieval_ms: number | null;
    llm_generation_ms: number | null;
    guardrails_ms: number | null;
  };
  tokens: {
    prompt: number;
    cached_prompt: number;
    reasoning: number;
    query_embedding?: number;
    query_embed?: number;
    total_billed: number;
  };
  accounting: { is_actual: boolean };
}

export interface DocumentSummary {
  document_id: string;
  source_name: string;
  source_type: string;
  chunk_count: number;
  created_at: string;
  updated_at?: string;
  original_format?: string;
  active_format?: "MARKDOWN" | string;
  converted_from?: "docx" | "md" | null;
  original_filename?: string;
  content_hash?: string;
  original_sha256?: string;
  ingest_count?: number;
  warnings?: string[];
  quarantined?: boolean;
}

export interface LatencyPercentiles {
  sample_size: number;
  p50_ms: number;
  p70_ms: number;
  p100_ms: number;
}

export interface BudgetedLatencyPercentiles extends LatencyPercentiles {
  within_budget_pct: number;
}

export interface LatencyStageBreakdown {
  stage: string;
  p50_ms: number;
  p70_ms: number;
  p100_ms: number;
}

export interface LatencyReport {
  // retrieval + guardrails only — the budgeted metric.
  pipeline_ex_llm: BudgetedLatencyPercentiles;
  // full /query request, informational only (no budget verdict).
  end_to_end: LatencyPercentiles;
  // generation stage alone, informational only (no budget verdict).
  llm: LatencyPercentiles;
  by_stage: LatencyStageBreakdown[];
}

export interface AuditLogEntry {
  id: string;
  request_id: string;
  actor: string;
  action: string;
  detail: Record<string, unknown>;
  created_at: string;
  ip_address: string | null;
}
