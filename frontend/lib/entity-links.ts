/**
 * Deep links for every entity in the Visitor -> Session -> Conversation ->
 * Turn -> Trace / Feedback / Document chain. Every id rendered in
 * a table, chart or drawer goes through `entityHref`, so a number can always
 * be traced to its rows and shared as a URL.
 */
export type EntityRef =
  | { kind: "visitor"; id: string }
  | { kind: "session"; id: string }
  | { kind: "conversation"; id: string }
  | { kind: "turn"; id: string; conversationId: string }
  | { kind: "trace"; id: string }
  | { kind: "feedback"; id: string }
  | { kind: "document"; id: string }
  | { kind: "chunk"; id: string; documentId: string }
  | {
      kind: "analytics";
      /** Analytics tab (`overview` when omitted). */
      tab?: string;
      intent?: string;
      quality?: string;
      issue?: string;
      /** Probable-person pseudonym (person_id). */
      person?: string;
      /** Visitors-page id (`client-<ip>` or a user UUID). */
      visitor?: string;
      /** Free-text search (a conversation id matches its own row). */
      q?: string;
    };

export type EntityKind = EntityRef["kind"];

export const ENTITY_KINDS: readonly EntityKind[] = [
  "visitor",
  "session",
  "conversation",
  "turn",
  "trace",
  "feedback",
  "document",
  "chunk",
  "analytics",
] as const;

export const ENTITY_LABEL: Record<EntityKind, string> = {
  visitor: "Visitor",
  session: "Session",
  conversation: "Conversation",
  turn: "Turn",
  trace: "Trace",
  feedback: "Feedback",
  document: "Document",
  chunk: "Chunk",
  analytics: "Analytics",
};

function withQuery(path: string, params: Record<string, string | undefined>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") query.set(key, value);
  }
  const qs = query.toString();
  return qs ? `${path}?${qs}` : path;
}

const seg = (value: string) => encodeURIComponent(value);

export function entityHref(ref: EntityRef): string {
  switch (ref.kind) {
    case "visitor":
      return withQuery("/admin/usage", { tab: "sessions", ip: ref.id });
    case "session":
      // A session's turns live on the Queries tab (Sessions has no session filter).
      return withQuery("/admin/usage", { tab: "queries", session: ref.id });
    case "conversation":
      return `/admin/conversations/${seg(ref.id)}`;
    case "turn":
      return withQuery(`/admin/conversations/${seg(ref.conversationId)}`, { turn: ref.id });
    case "trace":
      return withQuery("/admin/governance", { tab: "Traces", request_id: ref.id });
    case "feedback":
      return withQuery("/admin/feedback", { message_id: ref.id });
    case "document":
      return `/documents/${seg(ref.id)}`;
    case "chunk":
      return withQuery(`/documents/${seg(ref.documentId)}`, { chunk: ref.id });
    case "analytics":
      return withQuery("/admin/analytics", {
        tab: ref.tab,
        intent: ref.intent,
        quality: ref.quality,
        issue: ref.issue,
        person: ref.person,
        visitor: ref.visitor,
        q: ref.q,
      });
  }
}

/** The copyable identifier of an entity (null for filter-only links). */
export function entityId(ref: EntityRef): string | null {
  return ref.kind === "analytics" ? null : ref.id;
}
