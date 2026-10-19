/**
 * Routes of the merged Visitors & Conversations page. No "use client": the
 * /admin/users and /admin/conversations redirect pages (server) use these.
 */
/** The merged Visitors & Conversations page (replaces /admin/users and /admin/conversations). */
export const AUDIENCE_PAGE = "/admin/visitors";
export const AUDIENCE_PAGE_LABEL = "Visitors & Conversations";
export const AUDIENCE_TABS = ["visitors", "conversations"] as const;
export type AudienceTab = (typeof AUDIENCE_TABS)[number];

/** `?tab=` of the merged page; anything unknown is the Visitors tab. */
export function parseAudienceTab(value: string | null | undefined): AudienceTab {
  return value === "conversations" ? "conversations" : "visitors";
}

/** Merged page on `tab`, keeping the old page's query (old bookmarks keep their filters). */
export function audiencePageHref(
  tab: AudienceTab,
  query: Record<string, string | string[] | undefined> = {},
): string {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (key === "tab" || value === undefined) continue;
    for (const v of Array.isArray(value) ? value : [value]) qs.append(key, v);
  }
  if (tab !== "visitors") qs.set("tab", tab);
  const s = qs.toString();
  return s ? `${AUDIENCE_PAGE}?${s}` : AUDIENCE_PAGE;
}

/** Sessions created without a client IP (service accounts, server-to-server API calls, seeded data). */
export const NO_IP_HINT =
  "No client IP was captured for this session: it was created by a server-to-server or service-account call (or seeded data) that did not forward the visitor's IP.";
