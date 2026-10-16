"use client";

import React, { useMemo, useState } from "react";
import { AnimatePresence, m } from "motion/react";
import { Archive, ArrowDown, ArrowUp, Bot, CircleAlert, Lock, Plus, Save, Tags, Target } from "lucide-react";
import { TabGroup, type TabItem } from "@/components/admin/TabGroup";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/ToastProvider";
import { focusRing, iconButton, primaryButton } from "@/components/ui/styles";
import { normalizeHex } from "@/lib/brand-palette";
import { crossfade, fadeUp, useMotionVariants } from "@/lib/motion";
import { errorStatus, useApiQuery } from "@/lib/query-cache";
import {
  PROTECTED_TAXONOMY_CODES,
  SettingsApiError,
  TAXONOMY_CODE_RE,
  TAXONOMY_GROUP_MAX,
  TAXONOMY_KEY,
  TAXONOMY_LABEL_MAX,
  TAXONOMY_SORT_MAX,
  isDuplicateTaxonomyCode,
  settingsApi,
  taxonomyOfKind,
  type TaxonomyKind,
  type TaxonomyList,
  type TaxonomyRead,
  type TaxonomyUpdate,
} from "@/lib/settings-api";
import { brandRamp } from "@/lib/theme-tokens";
import { inputClass } from "./fields";

const KIND_TABS: TabItem<TaxonomyKind>[] = [
  { id: "intent", label: "Intents", icon: Target, description: "What the visitor asked about" },
  { id: "bot_quality", label: "Bot quality", icon: Bot, description: "How well the assistant answered" },
  { id: "issue", label: "Issues", icon: CircleAlert, description: "Why an answer fell short" },
];

const KIND_NOUN: Record<TaxonomyKind, string> = { intent: "intent", bot_quality: "bot-quality value", issue: "issue" };
const SORT_STEP = 10;

interface RowDraft {
  label: string;
  color: string;
  group: string;
}

const draftOf = (item: TaxonomyRead): RowDraft => ({ label: item.label, color: item.color ?? "", group: item.group ?? "" });

function rowChanges(item: TaxonomyRead, draft: RowDraft): TaxonomyUpdate {
  const out: TaxonomyUpdate = {};
  if (draft.label.trim() !== item.label) out.label = draft.label.trim();
  const color = draft.color.trim() ? draft.color.trim().toLowerCase() : null;
  if (color !== (item.color?.toLowerCase() ?? null)) out.color = color;
  const group = draft.group.trim() || null;
  if (group !== item.group) out.group = group;
  return out;
}

function rowError(draft: RowDraft): string | null {
  if (!draft.label.trim()) return "Label is required.";
  if (draft.label.trim().length > TAXONOMY_LABEL_MAX) return `Label is at most ${TAXONOMY_LABEL_MAX} characters.`;
  if (draft.color.trim() && !normalizeHex(draft.color)) return "Colour must be #RRGGBB.";
  if (draft.group.trim().length > TAXONOMY_GROUP_MAX) return `Group is at most ${TAXONOMY_GROUP_MAX} characters.`;
  return null;
}

const message = (error: unknown) => (error instanceof Error ? error.message : "The request failed.");

export function TaxonomyTab() {
  const toast = useToast();
  const query = useApiQuery<TaxonomyList>(TAXONOMY_KEY, (signal) => settingsApi.listTaxonomy(signal), { revalidateOnFocus: false });
  const items = useMemo(() => query.data?.items ?? [], [query.data]);
  const [kind, setKind] = useState<TaxonomyKind>("intent");
  const [showArchived, setShowArchived] = useState(false);
  const [drafts, setDrafts] = useState<Record<number, RowDraft>>({});
  const [busy, setBusy] = useState<Record<number, boolean>>({});
  const [archiveTarget, setArchiveTarget] = useState<TaxonomyRead | null>(null);
  const rowVariants = useMotionVariants(crossfade);
  const panelVariants = useMotionVariants(fadeUp);

  const ofKind = useMemo(() => taxonomyOfKind(items, kind), [items, kind]);
  const visible = useMemo(() => (showArchived ? ofKind : ofKind.filter((i) => i.is_active)), [ofKind, showArchived]);
  const archivedCount = ofKind.length - ofKind.filter((i) => i.is_active).length;

  const tabs = useMemo(
    () => KIND_TABS.map((t) => ({ ...t, badgeCount: items.filter((i) => i.kind === t.id && i.is_active).length })),
    [items],
  );

  const replaceItems = (updated: TaxonomyRead[]) =>
    query.setData((current) => {
      const byId = new Map(updated.map((u) => [u.id, u]));
      const list = current?.items ?? [];
      const merged = list.map((i) => byId.get(i.id) ?? i);
      for (const u of updated) if (!list.some((i) => i.id === u.id)) merged.push(u);
      return { items: merged };
    });

  const setBusyFor = (id: number, value: boolean) => setBusy((b) => ({ ...b, [id]: value }));

  async function saveRow(item: TaxonomyRead) {
    const draft = drafts[item.id];
    if (!draft) return;
    const problem = rowError(draft);
    if (problem) {
      toast.show({ title: `Cannot save ${item.code}`, description: problem, tone: "error" });
      return;
    }
    const changes = rowChanges(item, draft);
    if (!Object.keys(changes).length) return;
    setBusyFor(item.id, true);
    try {
      const updated = await settingsApi.updateTaxonomy(item.kind, item.code, changes);
      replaceItems([updated]);
      setDrafts((d) => {
        const next = { ...d };
        delete next[item.id];
        return next;
      });
      toast.show({ title: `${item.code} saved`, tone: "success" });
    } catch (error) {
      toast.show({ title: `Could not save ${item.code}`, description: message(error), tone: "error" });
      if (errorStatus(error) === 404) void query.refetch();
    } finally {
      setBusyFor(item.id, false);
    }
  }

  /** Moves a row and renumbers the kind (step 10); only rows whose order changed are patched. */
  async function move(item: TaxonomyRead, delta: -1 | 1) {
    const order = [...ofKind];
    const from = order.findIndex((i) => i.id === item.id);
    // Step over hidden (archived) neighbours so the move is visible.
    let to = from + delta;
    while (!showArchived && to >= 0 && to < order.length && !order[to].is_active) to += delta;
    if (from < 0 || to < 0 || to >= order.length) return;
    const [moved] = order.splice(from, 1);
    order.splice(to, 0, moved);
    const renumbered = order.map((i, index) => ({ ...i, sort_order: Math.min((index + 1) * SORT_STEP, TAXONOMY_SORT_MAX) }));
    const changed = renumbered.filter((i) => i.sort_order !== ofKind.find((o) => o.id === i.id)?.sort_order);
    const previous = query.data;
    replaceItems(changed); // optimistic
    try {
      const saved = await Promise.all(changed.map((i) => settingsApi.updateTaxonomy(i.kind, i.code, { sort_order: i.sort_order })));
      replaceItems(saved);
    } catch (error) {
      query.setData(previous);
      toast.show({ title: "Could not reorder", description: message(error), tone: "error" });
      void query.refetch();
    }
  }

  async function archive(item: TaxonomyRead) {
    setBusyFor(item.id, true);
    try {
      const updated = await settingsApi.archiveTaxonomy(item.kind, item.code);
      replaceItems([updated]);
      toast.show({
        title: `${item.code} archived`,
        description: "It no longer appears in filters or the classifier. Existing labels keep showing it.",
        tone: "success",
      });
    } catch (error) {
      toast.show({ title: `Could not archive ${item.code}`, description: message(error), tone: "error" });
    } finally {
      setBusyFor(item.id, false);
      setArchiveTarget(null);
    }
  }

  if (query.error && !query.data) {
    return (
      <ErrorState
        title="Could not load the taxonomy"
        message={message(query.error)}
        requestId={query.error instanceof SettingsApiError ? query.error.requestId : null}
        onRetry={() => void query.refetch()}
      />
    );
  }

  return (
    <m.div className="space-y-4" variants={panelVariants} initial="hidden" animate="visible">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div data-tour="settings-taxonomy-kinds">
          <TabGroup tabs={tabs} activeTab={kind} onChange={setKind} ariaLabel="Taxonomy kind" />
        </div>
        <label className="inline-flex cursor-pointer items-center gap-2 text-xs text-fg-muted" data-tour="settings-taxonomy-show-archived">
          <input
            type="checkbox"
            checked={showArchived}
            onChange={(e) => setShowArchived(e.target.checked)}
            className={`h-4 w-4 rounded border-line-strong accent-brand-600 ${focusRing}`}
          />
          Show archived{archivedCount ? ` (${archivedCount})` : ""}
        </label>
      </div>

      <p className="text-[11px] text-fg-subtle">
        Codes are permanent once created, so labels already stored on conversations never break. Archive an entry to remove it from
        filters and the classifier; conversations already labelled keep showing its label.
      </p>

      <div className="overflow-x-auto rounded-2xl border border-line bg-surface-card shadow-sm" data-tour="settings-taxonomy-table">
        {query.isLoading ? (
          <SkeletonRows rows={6} columns={5} label="Loading taxonomy" />
        ) : visible.length === 0 ? (
          <EmptyState compact title={`No ${KIND_NOUN[kind]} entries`} description="Add the first one below." icon={Tags} />
        ) : (
          <table className="w-full min-w-[760px] text-left text-xs" aria-label={`${KIND_TABS.find((t) => t.id === kind)?.label} taxonomy`}>
            <thead className="border-b border-line-brand/70 bg-surface-studio dark:border-brand-500/20 text-[11px] uppercase tracking-wider text-fg-subtle">
              <tr>
                <th scope="col" className="w-20 px-3 py-2 font-semibold">
                  Order
                </th>
                <th scope="col" className="px-3 py-2 font-semibold">
                  Code
                </th>
                <th scope="col" className="px-3 py-2 font-semibold">
                  Label
                </th>
                <th scope="col" className="px-3 py-2 font-semibold">
                  Colour
                </th>
                {kind === "intent" ? (
                  <th scope="col" className="px-3 py-2 font-semibold">
                    Group
                  </th>
                ) : null}
                <th scope="col" className="px-3 py-2 font-semibold">
                  Status
                </th>
                <th scope="col" className="px-3 py-2 text-right font-semibold">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              <AnimatePresence initial={false}>
                {visible.map((item, index) => {
                  const draft = drafts[item.id] ?? draftOf(item);
                  const dirty = Boolean(drafts[item.id]) && Object.keys(rowChanges(item, draft)).length > 0;
                  const problem = drafts[item.id] ? rowError(draft) : null;
                  const isProtected = PROTECTED_TAXONOMY_CODES[item.kind].has(item.code);
                  const archived = !item.is_active;
                  const edit = (patch: Partial<RowDraft>) => setDrafts((d) => ({ ...d, [item.id]: { ...draft, ...patch } }));
                  return (
                    <m.tr
                      key={item.id}
                      layout="position"
                      variants={rowVariants}
                      initial="hidden"
                      animate="visible"
                      exit="exit"
                      data-testid={`taxonomy-row-${item.code}`}
                      className={`border-b border-line last:border-0 transition-colors hover:bg-surface-muted/60 ${archived ? "opacity-60" : ""}`}
                    >
                      <td className="px-3 py-2">
                        <div className="flex items-center gap-0.5">
                          <button
                            type="button"
                            className={`${iconButton} h-7 w-7`}
                            aria-label={`Move ${item.code} up`}
                            disabled={index === 0 || archived}
                            onClick={() => void move(item, -1)}
                          >
                            <ArrowUp className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                          </button>
                          <button
                            type="button"
                            className={`${iconButton} h-7 w-7`}
                            aria-label={`Move ${item.code} down`}
                            disabled={index === visible.length - 1 || archived}
                            onClick={() => void move(item, 1)}
                          >
                            <ArrowDown className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                          </button>
                        </div>
                      </td>
                      <td className="px-3 py-2">
                        <span
                          className="inline-flex items-center gap-1 font-mono font-semibold text-fg"
                          title="Codes cannot be changed after creation"
                          data-testid={`code-${item.code}`}
                        >
                          <Lock className="h-3 w-3 text-fg-subtle" strokeWidth={1.75} aria-hidden="true" />
                          {item.code}
                        </span>
                      </td>
                      <td className="px-3 py-2">
                        <input
                          type="text"
                          value={draft.label}
                          disabled={archived}
                          maxLength={TAXONOMY_LABEL_MAX}
                          aria-label={`Label for ${item.code}`}
                          aria-invalid={Boolean(problem) && !draft.label.trim()}
                          onChange={(e) => edit({ label: e.target.value })}
                          className={`${inputClass} py-1.5 text-xs`}
                        />
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex items-center gap-1.5">
                          <input
                            type="color"
                            value={normalizeHex(draft.color) ?? brandRamp["600"].toLowerCase()}
                            disabled={archived}
                            aria-label={`Colour for ${item.code}`}
                            onChange={(e) => edit({ color: e.target.value })}
                            className="h-8 w-9 shrink-0 cursor-pointer rounded-md border border-line bg-surface-card p-0.5 disabled:cursor-not-allowed"
                          />
                          <span className="font-mono text-[11px] uppercase text-fg-muted">{draft.color || "Default"}</span>
                        </div>
                      </td>
                      {kind === "intent" ? (
                        <td className="px-3 py-2">
                          <input
                            type="text"
                            value={draft.group}
                            disabled={archived}
                            maxLength={TAXONOMY_GROUP_MAX}
                            aria-label={`Group for ${item.code}`}
                            onChange={(e) => edit({ group: e.target.value })}
                            className={`${inputClass} py-1.5 text-xs`}
                          />
                        </td>
                      ) : null}
                      <td className="px-3 py-2">
                        <span
                          className={`inline-flex rounded-full px-2 py-0.5 text-[11px] font-semibold ${
                            archived ? "bg-surface-muted text-fg-muted" : "bg-status-ok-bg text-status-ok-fg"
                          }`}
                        >
                          {archived ? "Archived" : "Active"}
                        </span>
                        {isProtected ? <span className="ml-1.5 text-[10px] text-fg-subtle">Core</span> : null}
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex items-center justify-end gap-1">
                          {dirty ? (
                            <button
                              type="button"
                              className={`${primaryButton} px-2.5 py-1.5`}
                              disabled={busy[item.id] || Boolean(problem)}
                              title={problem ?? undefined}
                              onClick={() => void saveRow(item)}
                            >
                              <Save className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                              Save
                            </button>
                          ) : null}
                          {!archived ? (
                            <button
                              type="button"
                              className={`${iconButton} h-7 w-7`}
                              aria-label={isProtected ? `${item.code} is a core code and cannot be archived` : `Archive ${item.code}`}
                              title={isProtected ? "Core code used by the rule stage; it cannot be archived" : "Archive"}
                              disabled={isProtected || busy[item.id]}
                              onClick={() => setArchiveTarget(item)}
                            >
                              <Archive className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                            </button>
                          ) : null}
                        </div>
                      </td>
                    </m.tr>
                  );
                })}
              </AnimatePresence>
            </tbody>
          </table>
        )}
      </div>

      <AddTaxonomyEntry
        kind={kind}
        items={items}
        nextOrder={Math.min(Math.max(0, ...ofKind.map((i) => i.sort_order)) + SORT_STEP, TAXONOMY_SORT_MAX)}
        onCreated={(created) => {
          replaceItems([created]);
          toast.show({ title: `${created.code} added`, tone: "success" });
        }}
      />

      <ConfirmDialog
        open={archiveTarget !== null}
        title={archiveTarget ? `Archive ${archiveTarget.code}?` : "Archive"}
        description="It disappears from filters and is no longer offered to the classifier. Conversations already labelled keep their label. Entries are never deleted, and the code cannot be reused."
        confirmLabel="Archive"
        tone="danger"
        onConfirm={() => (archiveTarget ? archive(archiveTarget) : undefined)}
        onCancel={() => setArchiveTarget(null)}
      />
    </m.div>
  );
}

interface AddProps {
  kind: TaxonomyKind;
  items: readonly TaxonomyRead[];
  nextOrder: number;
  onCreated: (item: TaxonomyRead) => void;
}

function AddTaxonomyEntry({ kind, items, nextOrder, onCreated }: AddProps) {
  const [code, setCode] = useState("");
  const [label, setLabel] = useState("");
  const [color, setColor] = useState("");
  const [group, setGroup] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const c = code.trim();
    if (!TAXONOMY_CODE_RE.test(c)) {
      setError("Code must start with a letter and use only A to Z, 0 to 9 and _ (at most 32 characters).");
      return;
    }
    if (isDuplicateTaxonomyCode(items, kind, c)) {
      setError(`${c} already exists for this kind (archived codes included); codes are never reused.`);
      return;
    }
    if (!label.trim()) {
      setError("Label is required.");
      return;
    }
    if (color.trim() && !normalizeHex(color)) {
      setError("Colour must be #RRGGBB.");
      return;
    }
    setPending(true);
    setError(null);
    try {
      const created = await settingsApi.createTaxonomy({
        kind,
        code: c,
        label: label.trim(),
        color: color.trim() ? color.trim().toLowerCase() : null,
        group: kind === "intent" && group.trim() ? group.trim() : null,
        sort_order: nextOrder,
      });
      onCreated(created);
      setCode("");
      setLabel("");
      setColor("");
      setGroup("");
    } catch (err) {
      setError(errorStatus(err) === 409 ? `${c} already exists for this kind; codes are never reused.` : message(err));
    } finally {
      setPending(false);
    }
  }

  return (
    <form onSubmit={submit} noValidate aria-label={`Add ${KIND_NOUN[kind]}`} data-tour="settings-taxonomy-add" className="rounded-2xl border border-line bg-surface-card p-4 shadow-sm">
      <h3 className="mb-3 text-xs font-bold text-fg">Add {KIND_NOUN[kind]}</h3>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-[10rem_minmax(0,1fr)_9rem_minmax(0,1fr)_auto] lg:items-end">
        <label className="space-y-1 text-xs font-semibold text-fg">
          <span>Code</span>
          <input
            type="text"
            value={code}
            maxLength={32}
            autoComplete="off"
            spellCheck={false}
            placeholder="e.g. BILLING"
            aria-invalid={Boolean(error)}
            onChange={(e) => {
              setCode(e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, ""));
              setError(null);
            }}
            className={`${inputClass} font-mono uppercase`}
          />
        </label>
        <label className="space-y-1 text-xs font-semibold text-fg">
          <span>Label</span>
          <input
            type="text"
            value={label}
            maxLength={TAXONOMY_LABEL_MAX}
            onChange={(e) => {
              setLabel(e.target.value);
              setError(null);
            }}
            className={inputClass}
          />
        </label>
        <label className="space-y-1 text-xs font-semibold text-fg">
          <span>Colour (optional)</span>
          <input
            type="text"
            value={color}
            maxLength={7}
            spellCheck={false}
            placeholder="#RRGGBB"
            onChange={(e) => setColor(e.target.value)}
            className={`${inputClass} font-mono uppercase`}
          />
        </label>
        {kind === "intent" ? (
          <label className="space-y-1 text-xs font-semibold text-fg">
            <span>Group (optional)</span>
            <input type="text" value={group} maxLength={TAXONOMY_GROUP_MAX} onChange={(e) => setGroup(e.target.value)} className={inputClass} />
          </label>
        ) : (
          <span className="hidden lg:block" />
        )}
        <button type="submit" className={primaryButton} disabled={pending}>
          <Plus className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
          {pending ? "Adding…" : "Add"}
        </button>
      </div>
      <AnimatePresence>
        {error ? (
          <m.p
            key="error"
            role="alert"
            variants={crossfade}
            initial="hidden"
            animate="visible"
            exit="exit"
            className="mt-2 text-[11px] font-medium text-status-danger-fg"
            data-testid="taxonomy-add-error"
          >
            {error}
          </m.p>
        ) : null}
      </AnimatePresence>
      <p className="mt-2 text-[11px] text-fg-subtle">
        The code is permanent. {secondaryHint(kind)}
      </p>
    </form>
  );
}

function secondaryHint(kind: TaxonomyKind): string {
  if (kind === "intent") return "Group intents (e.g. “Customer / prospect”) to roll them up in Analytics.";
  if (kind === "bot_quality") return "New values count as not handled until the classifier is updated.";
  return "Issues explain why an answer fell short.";
}
