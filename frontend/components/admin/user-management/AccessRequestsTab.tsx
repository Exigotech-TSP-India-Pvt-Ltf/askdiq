"use client";

/**
 * Access Requests tab (planchanges4 F1.4.3): the pending queue with approve
 * (role select) and reject (note), multi-select bulk approve, keyboard `a` /
 * `x` on a focused row, and the decision history. Approved and rejected rows
 * leave optimistically and animate out (`AnimatePresence`); a failure brings
 * them back.
 */
import React, { useEffect, useId, useRef, useState } from "react";
import { AnimatePresence, m } from "motion/react";
import { Check, Inbox, Keyboard, X } from "lucide-react";
import { TableCard } from "@/components/admin/TableCard";
import { Pagination } from "@/components/admin/Pagination";
import { Modal } from "@/components/ui/Modal";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/ToastProvider";
import { dangerButton, focusRing, primaryButton, secondaryButton } from "@/components/ui/styles";
import { fadeUp, useMotionVariants } from "@/lib/motion";
import { useApiQuery, useApiMutation, errorStatus, HTTP_CONFLICT } from "@/lib/query-cache";
import type { PageSize } from "@/lib/use-table-state";
import type { DeepLinkUpdate } from "@/lib/use-deep-link";
import {
  BULK_APPROVE_MAX,
  NOTE_MAX_LENGTH,
  ROLES,
  ROLE_LABEL,
  isSafeText,
  umKeys,
  userManagementApi,
  type AccessRequest,
  type AccessRequestPage,
  type AccessRequestStatus,
  type AccessRequestView,
  type BulkApproveResult,
  type Role,
} from "@/lib/user-management-api";
import { RoleBadge, fieldClass, formatWaiting } from "./parts";
import { RelativeTime } from "@/components/ui/RelativeTime";
import { useConflictToast } from "./use-user-actions";
import { PENDING_ACCESS_QUERY_KEY } from "@/lib/use-shell-notifications";

const DEFAULT_PAGE_SIZE: PageSize = 25;
const DEFAULT_ROLE: Role = "operator";
// The sidebar badge / bell count is refreshed too, not left for its 60 s poll.
const AFTER_DECISION = [
  umKeys.requestsPrefix,
  umKeys.usersPrefix,
  umKeys.statsPrefix,
  umKeys.auditPrefix,
  PENDING_ACCESS_QUERY_KEY,
] as const;

const VIEWS: { id: AccessRequestView; label: string }[] = [
  { id: "pending", label: "Queue" },
  { id: "approved", label: "Approved" },
  { id: "rejected", label: "Rejected" },
  { id: "all", label: "All decisions" },
];

const REQUEST_TONE: Record<AccessRequestStatus, string> = {
  pending: "bg-status-warn-bg text-status-warn-fg",
  approved: "bg-status-ok-bg text-status-ok-fg",
  rejected: "bg-status-danger-bg text-status-danger-fg",
};

function RequestStatus({ status }: { status: AccessRequestStatus }) {
  return (
    <span className={`inline-flex rounded-full px-2 py-0.5 text-[11px] font-semibold capitalize ${REQUEST_TONE[status]}`}>
      {status}
    </span>
  );
}

function withoutIds(page: AccessRequestPage | undefined, ids: ReadonlySet<string>): AccessRequestPage {
  if (!page) return page as unknown as AccessRequestPage;
  const items = page.items.filter((r) => !ids.has(r.id));
  return { ...page, items, total: Math.max(0, page.total - (page.items.length - items.length)) };
}

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName;
  if (tag === "SELECT" || tag === "TEXTAREA") return true;
  return tag === "INPUT" && (target as HTMLInputElement).type !== "checkbox";
}

interface AccessRequestsTabProps {
  view: AccessRequestView;
  update: (patch: DeepLinkUpdate) => void;
}

export function AccessRequestsTab({ view, update }: AccessRequestsTabProps) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<PageSize>(DEFAULT_PAGE_SIZE);
  useEffect(() => setPage(1), [view]);

  const offset = (page - 1) * pageSize;
  const key = umKeys.requests(view, pageSize, offset);
  const query = useApiQuery(key, (signal) => userManagementApi.listRequests(view, pageSize, offset, signal));
  const total = query.data?.total ?? 0;

  return (
    <div data-tour="user-management-requests-table">
    <TableCard
      title={view === "pending" ? "Access request queue" : "Decision history"}
      description={
        view === "pending"
          ? "Oldest first. Choose a role, then approve; reject with a note."
          : "Who decided what, and when."
      }
      headerActions={
        <div role="radiogroup" aria-label="Request view" data-tour="user-management-requests-view" className="inline-flex rounded-lg border border-line bg-surface-muted p-0.5">
          {VIEWS.map((v) => (
            <button
              key={v.id}
              type="button"
              role="radio"
              aria-checked={view === v.id}
              onClick={() => update({ view: v.id === "pending" ? null : v.id })}
              className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${focusRing} ${
                view === v.id ? "bg-surface-card text-fg shadow-sm" : "text-fg-muted hover:text-fg"
              }`}
            >
              {v.label}
            </button>
          ))}
        </div>
      }
      pagination={
        total > 0 ? (
          <Pagination
            page={page}
            pageCount={Math.max(1, Math.ceil(total / pageSize))}
            totalItems={total}
            pageSize={pageSize}
            onPageChange={setPage}
            onPageSizeChange={(size) => {
              setPageSize(size);
              setPage(1);
            }}
          />
        ) : undefined
      }
    >
      {query.isLoading ? (
        <SkeletonRows rows={5} columns={6} label="Loading access requests" />
      ) : query.error && !query.data ? (
        <ErrorState message={(query.error as Error).message} onRetry={() => void query.refetch()} />
      ) : view === "pending" ? (
        <PendingQueue listKey={key} items={query.data?.items ?? []} />
      ) : (
        <DecisionHistory items={query.data?.items ?? []} />
      )}
    </TableCard>
    </div>
  );
}

function PendingQueue({ listKey, items }: { listKey: string; items: readonly AccessRequest[] }) {
  const toast = useToast();
  const onConflict = useConflictToast();
  const rowVariants = useMotionVariants(fadeUp);
  const [roles, setRoles] = useState<Record<string, Role>>({});
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [bulkRole, setBulkRole] = useState<Role>(DEFAULT_ROLE);
  const [rejecting, setRejecting] = useState<AccessRequest | null>(null);
  const selectAllId = useId();
  const bulkRoleId = useId();
  const hintId = useId();

  // Drop selections for rows that left the queue.
  useEffect(() => {
    setSelected((prev) => {
      const live = new Set(items.map((r) => r.id));
      const next = new Set(Array.from(prev).filter((id) => live.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [items]);

  const roleFor = (r: AccessRequest): Role => roles[r.id] ?? r.requested_role ?? DEFAULT_ROLE;

  const failure = (title: string) => (error: unknown) => {
    if (errorStatus(error) === HTTP_CONFLICT) return;
    toast.show({ title, description: error instanceof Error ? error.message : undefined, tone: "error" });
  };

  const approve = useApiMutation<{ request: AccessRequest; role: Role }, AccessRequest, AccessRequestPage>({
    mutationFn: ({ request, role }) => userManagementApi.approve(request.id, role),
    optimistic: { key: listKey, update: (page, { request }) => withoutIds(page, new Set([request.id])) },
    invalidate: AFTER_DECISION,
    onConflict,
    onSuccess: (r, { role }) =>
      toast.show({ title: "Access approved", description: `${r.user_email} is now ${ROLE_LABEL[role]}.`, tone: "success" }),
    onError: failure("Approve failed"),
  });

  const reject = useApiMutation<{ request: AccessRequest; note: string }, AccessRequest, AccessRequestPage>({
    mutationFn: ({ request, note }) => userManagementApi.reject(request.id, note),
    optimistic: { key: listKey, update: (page, { request }) => withoutIds(page, new Set([request.id])) },
    invalidate: AFTER_DECISION,
    onConflict,
    onSuccess: (r) => toast.show({ title: "Access rejected", description: r.user_email, tone: "success" }),
    onError: failure("Reject failed"),
  });

  const bulk = useApiMutation<{ ids: string[]; role: Role }, BulkApproveResult, AccessRequestPage>({
    mutationFn: ({ ids, role }) => userManagementApi.bulkApprove(ids, role),
    optimistic: { key: listKey, update: (page, { ids }) => withoutIds(page, new Set(ids)) },
    invalidate: AFTER_DECISION,
    onConflict,
    onSuccess: (res, { role }) => {
      setSelected(new Set());
      toast.show({
        title: `Approved ${res.approved} request${res.approved === 1 ? "" : "s"}`,
        description: `All as ${ROLE_LABEL[role]}.`,
        tone: "success",
      });
    },
    onError: failure("Bulk approve failed"),
  });

  const allSelected = items.length > 0 && items.every((r) => selected.has(r.id));
  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const onRowKeyDown = (e: React.KeyboardEvent, r: AccessRequest) => {
    if (e.altKey || e.ctrlKey || e.metaKey || isTypingTarget(e.target)) return;
    const k = e.key.toLowerCase();
    if (k === "a") {
      e.preventDefault();
      void approve.mutate({ request: r, role: roleFor(r) });
    } else if (k === "x") {
      e.preventDefault();
      setRejecting(r);
    }
  };

  if (items.length === 0) {
    return <EmptyState icon={Inbox} title="No pending requests" description="New access requests appear here, oldest first." />;
  }

  const selectedIds = items.filter((r) => selected.has(r.id)).map((r) => r.id);

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-2.5 sm:px-5">
        <p id={hintId} className="flex items-center gap-1.5 text-[11px] text-fg-subtle">
          <Keyboard className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
          Focus a row, then press <kbd className="rounded bg-surface-muted px-1 font-mono text-fg-muted">A</kbd> to approve
          or <kbd className="rounded bg-surface-muted px-1 font-mono text-fg-muted">X</kbd> to reject.
        </p>
        <div className="flex items-center gap-2" data-testid="um-bulk-bar" data-tour="user-management-requests-bulk">
          <span className="text-xs text-fg-muted tabular-nums">{selectedIds.length} selected</span>
          <label htmlFor={bulkRoleId} className="sr-only">
            Role for bulk approval
          </label>
          <select id={bulkRoleId} value={bulkRole} onChange={(e) => setBulkRole(e.target.value as Role)} className={fieldClass}>
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {ROLE_LABEL[r]}
              </option>
            ))}
          </select>
          <button
            type="button"
            className={primaryButton}
            disabled={selectedIds.length === 0 || selectedIds.length > BULK_APPROVE_MAX || bulk.isPending}
            onClick={() => void bulk.mutate({ ids: selectedIds, role: bulkRole })}
          >
            <Check className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            Approve selected
          </button>
        </div>
      </div>
      <table className="w-full border-collapse text-left text-xs" aria-describedby={hintId}>
        <thead className="border-b border-line-brand/70 bg-surface-studio dark:border-brand-500/20 text-fg-muted">
          <tr>
            <th scope="col" className="w-10 px-4 py-2.5">
              <label htmlFor={selectAllId} className="sr-only">
                Select all requests
              </label>
              <input
                id={selectAllId}
                type="checkbox"
                checked={allSelected}
                onChange={() => setSelected(allSelected ? new Set() : new Set(items.map((r) => r.id)))}
                className="h-3.5 w-3.5 rounded border-line accent-brand-600"
              />
            </th>
            <th scope="col" className="px-3 py-2.5 font-semibold">User</th>
            <th scope="col" className="px-3 py-2.5 font-semibold">Requested</th>
            <th scope="col" className="px-3 py-2.5 font-semibold">Justification</th>
            <th scope="col" className="px-3 py-2.5 font-semibold">Waiting</th>
            <th scope="col" className="px-3 py-2.5 font-semibold">Approve as</th>
            <th scope="col" className="px-3 py-2.5 text-right font-semibold">Decision</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line text-fg-muted">
          <AnimatePresence initial={false}>
            {items.map((r) => (
              <m.tr
                key={r.id}
                data-testid={`request-row-${r.id}`}
                tabIndex={0}
                aria-label={`Access request from ${r.user_name || r.user_email}`}
                onKeyDown={(e) => onRowKeyDown(e, r)}
                variants={rowVariants}
                initial="hidden"
                animate="visible"
                exit="exit"
                className={`transition-colors hover:bg-brand-50/40 dark:hover:bg-brand-950/40 ${focusRing}`}
              >
                <td className="px-4 py-2.5">
                  <input
                    type="checkbox"
                    aria-label={`Select ${r.user_email}`}
                    checked={selected.has(r.id)}
                    onChange={() => toggle(r.id)}
                    className="h-3.5 w-3.5 rounded border-line accent-brand-600"
                  />
                </td>
                <td className="px-3 py-2.5">
                  <p className="font-medium text-fg">{r.user_name || "—"}</p>
                  <p className="text-fg-subtle">{r.user_email}</p>
                </td>
                <td className="px-3 py-2.5">
                  <RoleBadge role={r.requested_role} />
                </td>
                <td className="max-w-xs px-3 py-2.5">
                  <p className="line-clamp-2 text-fg-muted">{r.justification || "—"}</p>
                </td>
                <td className="whitespace-nowrap px-3 py-2.5 tabular-nums">
                  {formatWaiting(r.waiting_seconds)}
                  <RelativeTime value={r.created_at} display="absolute" className="block text-[11px] text-fg-subtle" />
                </td>
                <td className="px-3 py-2.5">
                  <select
                    aria-label={`Role for ${r.user_email}`}
                    value={roleFor(r)}
                    onChange={(e) => setRoles((prev) => ({ ...prev, [r.id]: e.target.value as Role }))}
                    className={fieldClass}
                  >
                    {ROLES.map((role) => (
                      <option key={role} value={role}>
                        {ROLE_LABEL[role]}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="px-3 py-2.5">
                  <div className="flex justify-end gap-1.5">
                    <button
                      type="button"
                      className={primaryButton}
                      onClick={() => void approve.mutate({ request: r, role: roleFor(r) })}
                      aria-label={`Approve ${r.user_email}`}
                    >
                      <Check className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                      Approve
                    </button>
                    <button
                      type="button"
                      className={secondaryButton}
                      onClick={() => setRejecting(r)}
                      aria-label={`Reject ${r.user_email}`}
                    >
                      <X className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                      Reject
                    </button>
                  </div>
                </td>
              </m.tr>
            ))}
          </AnimatePresence>
        </tbody>
      </table>
      <RejectDialog
        request={rejecting}
        onCancel={() => setRejecting(null)}
        onReject={(note) => {
          if (rejecting) void reject.mutate({ request: rejecting, note });
          setRejecting(null);
        }}
      />
    </>
  );
}

function RejectDialog({
  request,
  onCancel,
  onReject,
}: {
  request: AccessRequest | null;
  onCancel: () => void;
  onReject: (note: string) => void;
}) {
  const [note, setNote] = useState("");
  const noteRef = useRef<HTMLTextAreaElement>(null);
  const titleId = useId();
  const noteId = useId();
  useEffect(() => {
    if (!request) setNote("");
  }, [request]);
  const trimmed = note.trim();
  const valid = trimmed.length > 0 && trimmed.length <= NOTE_MAX_LENGTH && isSafeText(trimmed);

  return (
    <Modal open={request !== null} onClose={onCancel} labelledBy={titleId} initialFocusRef={noteRef} className="max-w-md p-5">
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (valid) onReject(trimmed);
        }}
      >
        <div>
          <h2 id={titleId} className="text-sm font-semibold text-fg">
            Reject access request
          </h2>
          <p className="mt-1 text-xs text-fg-muted">{request?.user_email} sees this note on the Access Required page.</p>
        </div>
        <div className="space-y-1.5">
          <label htmlFor={noteId} className="block text-xs font-medium text-fg-muted">
            Note
          </label>
          <textarea
            id={noteId}
            ref={noteRef}
            rows={3}
            value={note}
            maxLength={NOTE_MAX_LENGTH}
            onChange={(e) => setNote(e.target.value)}
            className={`w-full ${fieldClass}`}
          />
        </div>
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onCancel} className={secondaryButton}>
            Cancel
          </button>
          <button type="submit" disabled={!valid} className={dangerButton}>
            Reject
          </button>
        </div>
      </form>
    </Modal>
  );
}

function DecisionHistory({ items }: { items: readonly AccessRequest[] }) {
  if (items.length === 0) return <EmptyState icon={Inbox} title="No decisions yet" />;
  return (
    <table className="w-full border-collapse text-left text-xs">
      <thead className="border-b border-line-brand/70 bg-surface-studio dark:border-brand-500/20 text-fg-muted">
        <tr>
          <th scope="col" className="px-4 py-2.5 font-semibold">User</th>
          <th scope="col" className="px-3 py-2.5 font-semibold">Requested</th>
          <th scope="col" className="px-3 py-2.5 font-semibold">Decision</th>
          <th scope="col" className="px-3 py-2.5 font-semibold">Decided by</th>
          <th scope="col" className="px-3 py-2.5 font-semibold">Submitted</th>
          <th scope="col" className="px-3 py-2.5 font-semibold">Decided</th>
          <th scope="col" className="px-3 py-2.5 font-semibold">Note</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-line text-fg-muted">
        {items.map((r) => (
          <tr key={r.id} className="transition-colors hover:bg-brand-50/60 dark:hover:bg-brand-500/[0.07]">
            <td className="px-4 py-2.5">
              <p className="font-medium text-fg">{r.user_name || "—"}</p>
              <p className="text-fg-subtle">{r.user_email}</p>
            </td>
            <td className="px-3 py-2.5">
              <RoleBadge role={r.requested_role} />
            </td>
            <td className="px-3 py-2.5">
              <RequestStatus status={r.status} />
            </td>
            <td className="px-3 py-2.5">{r.decided_by_email ?? "—"}</td>
            <td className="whitespace-nowrap px-3 py-2.5">
              <RelativeTime value={r.created_at} />
            </td>
            <td className="whitespace-nowrap px-3 py-2.5">
              <RelativeTime value={r.decided_at} />
            </td>
            <td className="max-w-xs px-3 py-2.5">{r.decision_note ?? "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
