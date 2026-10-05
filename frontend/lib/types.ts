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
  grounded?: boolean | null;
  guardrail_flags?: string[] | null;
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
}

export interface DocumentSummary {
  document_id: string;
  source_name: string;
  source_type: string;
  chunk_count: number;
  created_at: string;
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
