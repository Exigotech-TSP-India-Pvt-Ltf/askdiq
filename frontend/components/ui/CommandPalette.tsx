"use client";

import React, { useEffect, useId, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Command, CornerDownLeft, FileSearch, Loader2, Search } from "lucide-react";
import { Modal } from "@/components/ui/Modal";

export interface PaletteItem {
  id: string;
  label: string;
  href: string;
  group?: string;
  icon?: React.ComponentType<{ className?: string; strokeWidth?: string | number }>;
  keywords?: string[];
  /** Secondary text, e.g. the entity type or id. */
  hint?: string;
  /** Permission needed to see the item; checked with `canAccess`. */
  permission?: string;
  /** Shown when any one of these is allowed. */
  anyPermission?: readonly string[];
}

/**
 * Server search hook-up point for the future `/admin/search` endpoint. Receives
 * the debounced query and an AbortSignal that fires when the query changes.
 */
export type PaletteSearchProvider = (query: string, signal: AbortSignal) => Promise<PaletteItem[]>;

/** Debounce before calling the search provider. */
export const SEARCH_DEBOUNCE_MS = 200;
const MIN_SEARCH_LENGTH = 2;

interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
  pages: PaletteItem[];
  searchProvider?: PaletteSearchProvider;
  /** Permission filter: items with a `permission` are shown only when this returns true. */
  canAccess?: (permission: string) => boolean;
  /** Defaults to `router.push`. */
  onNavigate?: (href: string) => void;
}

function matches(item: PaletteItem, query: string): boolean {
  if (!query) return true;
  const haystack = [item.label, item.hint ?? "", ...(item.keywords ?? [])].join(" ").toLowerCase();
  return query
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((word) => haystack.includes(word));
}

/**
 * Ctrl/Cmd+K command palette: jump to a page now, entity
 * search once `/admin/search` exists (pass `searchProvider`). Arrow keys move,
 * Enter opens, Esc closes; results are filtered by permission.
 */
export function CommandPalette({ open, onClose, pages, searchProvider, canAccess, onNavigate }: CommandPaletteProps) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [remote, setRemote] = useState<PaletteItem[]>([]);
  const [searching, setSearching] = useState(false);
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const titleId = useId();
  const listId = useId();

  useEffect(() => {
    if (open) return;
    setQuery("");
    setRemote([]);
    setActive(0);
  }, [open]);

  useEffect(() => {
    const q = query.trim();
    if (!open || !searchProvider || q.length < MIN_SEARCH_LENGTH) {
      setRemote([]);
      setSearching(false);
      return undefined;
    }
    const controller = new AbortController();
    setSearching(true);
    const timer = setTimeout(() => {
      searchProvider(q, controller.signal)
        .then((items) => {
          if (!controller.signal.aborted) setRemote(items);
        })
        .catch(() => {
          if (!controller.signal.aborted) setRemote([]);
        })
        .finally(() => {
          if (!controller.signal.aborted) setSearching(false);
        });
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [query, open, searchProvider]);

  const results = useMemo(() => {
    const can = (permission: string) => (canAccess ? canAccess(permission) : true);
    const allowed = (item: PaletteItem) =>
      item.anyPermission ? item.anyPermission.some(can) : !item.permission || can(item.permission);
    const q = query.trim();
    const pageHits = pages.filter((p) => allowed(p) && matches(p, q)).map((p) => ({ ...p, group: p.group ?? "Pages" }));
    const remoteHits = remote.filter(allowed).map((r) => ({ ...r, group: r.group ?? "Results" }));
    return [...pageHits, ...remoteHits];
  }, [pages, remote, query, canAccess]);

  useEffect(() => {
    setActive((index) => Math.min(index, Math.max(0, results.length - 1)));
  }, [results.length]);

  function go(item: PaletteItem | undefined) {
    if (!item) return;
    onClose();
    if (onNavigate) onNavigate(item.href);
    else router.push(item.href);
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (results.length === 0) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((i) => (i + 1) % results.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((i) => (i - 1 + results.length) % results.length);
    } else if (event.key === "Home") {
      event.preventDefault();
      setActive(0);
    } else if (event.key === "End") {
      event.preventDefault();
      setActive(results.length - 1);
    } else if (event.key === "Enter") {
      event.preventDefault();
      go(results[active]);
    }
  }

  const optionId = (index: number) => `${listId}-opt-${index}`;
  let lastGroup = "";

  return (
    <Modal
      open={open}
      onClose={onClose}
      labelledBy={titleId}
      initialFocusRef={inputRef}
      placement="top"
      className="max-w-xl overflow-hidden"
    >
      <h2 id={titleId} className="sr-only">
        Command palette
      </h2>
      <div className="flex items-center gap-2.5 border-b border-line px-4">
        <Search className="h-[18px] w-[18px] shrink-0 text-fg-subtle" strokeWidth={1.75} aria-hidden="true" />
        <input
          ref={inputRef}
          role="combobox"
          aria-expanded="true"
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={results.length ? optionId(active) : undefined}
          aria-label="Search pages and records"
          placeholder={searchProvider ? "Jump to a page, or search ids, emails and documents…" : "Jump to a page…"}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setActive(0);
          }}
          onKeyDown={onKeyDown}
          className="h-12 w-full bg-transparent text-sm text-fg placeholder:text-fg-subtle focus:outline-none"
        />
        {searching ? (
          <Loader2 className="h-4 w-4 shrink-0 animate-spin text-fg-subtle" strokeWidth={1.75} aria-label="Searching" />
        ) : null}
      </div>
      <ul id={listId} role="listbox" aria-label="Results" className="max-h-[50vh] overflow-y-auto p-1.5">
        {results.length === 0 ? (
          <li role="presentation" className="flex flex-col items-center gap-1.5 px-4 py-8 text-center text-xs text-fg-subtle">
            <FileSearch className="h-[18px] w-[18px]" strokeWidth={1.75} aria-hidden="true" />
            {searching ? "Searching…" : "No matching pages or records"}
          </li>
        ) : (
          results.map((item, index) => {
            const Icon = item.icon;
            const header = item.group !== lastGroup ? item.group : null;
            lastGroup = item.group ?? "";
            const selected = index === active;
            return (
              <React.Fragment key={`${item.group}-${item.id}`}>
                {header ? (
                  <li role="presentation" className="px-2.5 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wider text-fg-subtle">
                    {header}
                  </li>
                ) : null}
                <li
                  id={optionId(index)}
                  role="option"
                  aria-selected={selected}
                  onMouseMove={() => setActive(index)}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => go(item)}
                  className={`flex cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-2 text-xs transition-colors ${
                    selected ? "bg-brand-50 text-brand-800 dark:bg-brand-950/60 dark:text-brand-200" : "text-fg-muted"
                  }`}
                >
                  {Icon ? (
                    <Icon className={`h-4 w-4 shrink-0 ${selected ? "text-brand-600 dark:text-brand-400" : "text-fg-subtle"}`} strokeWidth={1.75} />
                  ) : null}
                  <span className="min-w-0 flex-1 truncate font-medium">{item.label}</span>
                  {item.hint ? <span className="truncate font-mono text-[11px] text-fg-subtle">{item.hint}</span> : null}
                  {selected ? <CornerDownLeft className="h-3.5 w-3.5 shrink-0" strokeWidth={1.75} aria-hidden="true" /> : null}
                </li>
              </React.Fragment>
            );
          })
        )}
      </ul>
      <div className="flex items-center justify-between border-t border-line bg-surface-muted/50 px-4 py-2 text-[11px] text-fg-subtle">
        <span className="flex items-center gap-1">
          <Command className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
          <span>↑ ↓ to move · Enter to open · Esc to close</span>
        </span>
        <span>? for shortcuts</span>
      </div>
    </Modal>
  );
}
