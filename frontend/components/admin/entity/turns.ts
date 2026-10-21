import type { ConversationDetail, TurnDetail } from "@/lib/entity-stats-api";

/** One question / answer pair of a conversation, plus B7.2 per-turn detail when deployed. */
export interface TurnView {
  turnIndex: number;
  question: string | null;
  answer: string | null;
  createdAt: string | null;
  requestId: string | null;
  assistantMessageId: string | null;
  grounded: boolean | null;
  guardrailFlags: string[];
  /** The preview batch cut the question or answer (`ConversationMessage.content_truncated`). */
  truncated: boolean;
  detail: TurnDetail | null;
}

/** Merges the message list (always present) with B7.2 per-turn detail (when deployed). */
export function buildTurns(data: ConversationDetail | undefined): TurnView[] {
  if (!data) return [];
  const byIndex = new Map<number, TurnView>();
  for (const m of data.messages) {
    const t = byIndex.get(m.turn_index) ?? {
      turnIndex: m.turn_index,
      question: null,
      answer: null,
      createdAt: m.created_at ?? null,
      requestId: null,
      assistantMessageId: null,
      grounded: null,
      guardrailFlags: [],
      truncated: false,
      detail: null,
    };
    if (m.content_truncated) t.truncated = true;
    if (m.role === "user") t.question = m.content;
    else {
      t.answer = m.content;
      t.assistantMessageId = m.id;
      t.requestId = m.request_id ?? t.requestId;
      t.grounded = m.grounded ?? null;
      t.guardrailFlags = m.guardrail_flags ?? [];
    }
    byIndex.set(m.turn_index, t);
  }
  for (const d of data.turns ?? []) {
    const t = byIndex.get(d.turn_index);
    if (t) {
      t.detail = d;
      t.requestId = t.requestId ?? d.request_id;
      t.assistantMessageId = t.assistantMessageId ?? d.assistant_message_id;
    }
  }
  return [...byIndex.values()].sort((a, b) => a.turnIndex - b.turnIndex);
}

