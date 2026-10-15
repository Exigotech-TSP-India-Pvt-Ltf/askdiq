/**
 * In-page deep links for the entity pages (newplan §1). `lib/entity-links.ts`
 * owns the cross-page entity chain; these are the list filters and detail
 * routes the Visitors / Conversations / Documents pages read back from the
 * URL, so every chart click is a shareable, back-button-safe URL.
 */
"use client";

import { useCallback } from "react";
import { useSearchParams } from "next/navigation";
import { HOURS_PER_DAY, WEEKDAYS, binLabel, type TurnsBin } from "./entity-stats-api";

const HOUR = (h: number) => String(h).padStart(2, "0");

function withQuery(path: string, params: Record<string, string | number | null | undefined>): string {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") qs.set(key, String(value));
  }
  const s = qs.toString();
  return s ? `${path}?${s}` : path;
}

/** Visitor detail (`client-<ip>` or a user UUID). */
export function visitorDetailHref(id: string): string {
  return `/admin/users/${encodeURIComponent(id)}`;
}

/** Visitors filtered by a heatmap cell. */
export function visitorsHref(filter: { weekday?: number | null; hour?: number | null } = {}): string {
  return withQuery("/admin/users", { weekday: filter.weekday, hour: filter.hour });
}

export function heatFilterLabel(weekday: number, hour: number): string {
  return `${WEEKDAYS[weekday]} ${HOUR(hour)}:00–${HOUR((hour + 1) % HOURS_PER_DAY)}:00`;
}

export interface ConversationFilter {
  ip?: string | null;
  min_turns?: number | null;
  max_turns?: number | null;
  outcome?: string | null;
  day?: string | null;
  preview?: string | null;
}

/** Conversations list, optionally filtered and with a preview drawer open. */
export function conversationsHref(filter: ConversationFilter = {}): string {
  return withQuery("/admin/conversations", { ...filter });
}

export function turnsFilterLabel(bin: Pick<TurnsBin, "min_turns" | "max_turns">): string {
  return `${binLabel(bin)} turn${bin.max_turns === bin.min_turns && bin.min_turns === 1 ? "" : "s"}`;
}


/** Global URL state that follows the user across admin pages (newplan §1, F3.6). */
export const CARRIED_PARAMS = ["range", "from", "to"] as const;

/**
 * Copies the global range params from `search` onto an in-app admin `href`
 * (params already on `href` win), so a click-through keeps the same window.
 * Audience is global through AudienceProvider and needs no URL carry.
 */
export function carryParams(href: string, search: string | null | undefined): string {
  if (!search || !href.startsWith("/admin")) return href;
  const from = new URLSearchParams(search);
  const [path, query = ""] = href.split("?");
  const target = new URLSearchParams(query);
  let changed = false;
  for (const key of CARRIED_PARAMS) {
    const value = from.get(key);
    if (value && !target.has(key)) {
      target.set(key, value);
      changed = true;
    }
  }
  if (!changed) return href;
  return `${path}?${target.toString()}`;
}

/** `carryParams` bound to the current URL. */
export function useCarryHref(): (href: string) => string {
  const params = useSearchParams();
  const search = params?.toString() ?? "";
  return useCallback((href: string) => carryParams(href, search), [search]);
}
