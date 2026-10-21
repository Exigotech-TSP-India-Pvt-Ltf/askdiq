import type { GovernanceLogItem, IpSource } from "@/lib/governance-api";

/** Events written by background jobs carry this request id / actor. */
const SYSTEM_ID = "system";

const LINKED_HINT: Record<Exclude<IpSource, "recorded">, string> = {
  session: "Not stored on this event: taken from the sign-in session it opened.",
  request: "Not stored on this event: taken from another event of the same request.",
};

export function missingIpReason(log: Pick<GovernanceLogItem, "request_id" | "user_id">): string {
  return log.request_id === SYSTEM_ID || log.user_id === SYSTEM_ID
    ? "Not recorded: system job, no client."
    : "Not recorded: no client IP was captured for this event.";
}

/**
 * Logs "Client IP" cell. A recorded IP shows as is; a correlated one (server
 * side, never stored) carries a "linked" hint; otherwise "Not recorded" with why.
 */
export function ClientIpCell({ log }: { log: GovernanceLogItem }) {
  const source = log.ip_source ?? (log.ip_address ? "recorded" : null);
  if (!log.ip_address || !source) {
    const reason = missingIpReason(log);
    return (
      <span className="whitespace-nowrap text-[11px] text-fg-subtle" title={reason} aria-label={reason}>
        Not recorded
      </span>
    );
  }
  if (source === "recorded") {
    return <span className="font-mono text-[11px] text-fg-muted">{log.ip_address}</span>;
  }
  const hint = LINKED_HINT[source];
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap" title={hint}>
      <span className="font-mono text-[11px] text-fg-muted">{log.ip_address}</span>
      <span className="rounded border border-border px-1 text-[10px] leading-4 text-fg-subtle">linked</span>
      <span className="sr-only">{hint}</span>
    </span>
  );
}
