/**
 * Active alerts for the header bell (backend `GET /admin/alerts`,
 * `app/api/routes/alerts.py`; needs `dashboard:read`). Calls go through
 * `/api/proxy`; the response is validated field by field, never trusted.
 */
import { ApiError } from "@/lib/api";

const PROXY = "/api/proxy";
/** The bell shows at most this many alerts (newest first). */
export const BELL_ALERT_LIMIT = 10;

export type AlertSeverity = "info" | "warning" | "critical";

export interface ActiveAlert {
  id: string;
  title: string;
  summary: string;
  severity: AlertSeverity;
  lastSeenAt: string;
  consolePath: string;
}

const SEVERITIES: readonly AlertSeverity[] = ["info", "warning", "critical"];

function str(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

/** Internal console path only (never an absolute or protocol-relative URL). */
function safePath(value: unknown): string {
  const path = str(value);
  return path && path.startsWith("/") && !path.startsWith("//") ? path : "/admin/governance";
}

export function parseAlerts(raw: unknown): ActiveAlert[] {
  const items = (raw as { items?: unknown } | null)?.items;
  if (!Array.isArray(items)) return [];
  const out: ActiveAlert[] = [];
  for (const item of items) {
    const r = (item ?? {}) as Record<string, unknown>;
    const id = str(r.id);
    const title = str(r.title);
    const severity = SEVERITIES.find((s) => s === r.severity);
    if (!id || !title || !severity) continue;
    out.push({
      id,
      title,
      summary: str(r.summary) ?? "",
      severity,
      lastSeenAt: str(r.last_seen_at) ?? "",
      consolePath: safePath(r.console_path),
    });
  }
  return out;
}

export async function fetchActiveAlerts(signal?: AbortSignal): Promise<ActiveAlert[]> {
  const res = await fetch(`${PROXY}/admin/alerts?status=active&limit=${BELL_ALERT_LIMIT}`, {
    credentials: "same-origin",
    cache: "no-store",
    headers: { Accept: "application/json" },
    signal,
  });
  if (!res.ok) throw new ApiError(res.statusText || "Request failed", res.status);
  return parseAlerts(await res.json());
}
