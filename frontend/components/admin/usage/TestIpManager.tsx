"use client";

import React, { useEffect, useState, useMemo } from "react";
import { createPortal } from "react-dom";
import {
  Shield,
  Plus,
  Trash2,
  RefreshCw,
  AlertTriangle,
  Info,
  X,
  CheckCircle2,
} from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { DataTable, type ColumnDef } from "../DataTable";
import { RelativeTime } from "@/components/ui/RelativeTime";
import { Pagination } from "../Pagination";
import { TableToolbar } from "../TableToolbar";

interface TestIpRow {
  id: string;
  ip_address: string;
  description: string | null;
  is_active: boolean;
  created_at: string | null;
}

interface TestIpManagerProps {
  token: string;
  isOpen: boolean;
  onClose: () => void;
  onConfigChanged?: () => void;
}

export function TestIpManager({
  token,
  isOpen,
  onClose,
  onConfigChanged,
}: TestIpManagerProps) {
  const [data, setData] = useState<{
    default_ips: string[];
    custom_ips: TestIpRow[];
    effective_ips: string[];
    classification_note?: string;
  } | null>(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [newIp, setNewIp] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  // Client table state for custom IPs
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [confirmDeleteIp, setConfirmDeleteIp] = useState<string | null>(null);

  const fetchConfig = React.useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.getTestIps(token);
      setData(res);
    } catch (err: any) {
      setError(err instanceof ApiError ? err.message : "Failed to load test IP rules.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    if (isOpen) {
      fetchConfig();
      setSubmitError(null);
      setSuccessMsg(null);
    }
  }, [isOpen, fetchConfig]);

  const handleAddIp = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!token || !newIp.trim()) return;

    setIsSubmitting(true);
    setSubmitError(null);
    setSuccessMsg(null);

    try {
      await api.addTestIp(token, newIp.trim(), newDesc.trim() || undefined);
      setNewIp("");
      setNewDesc("");
      setSuccessMsg(`Test IP rule "${newIp.trim()}" added successfully.`);
      await fetchConfig();
      onConfigChanged?.();
      setTimeout(() => setSuccessMsg(null), 3000);
    } catch (err: any) {
      setSubmitError(
        err instanceof ApiError
          ? err.message
          : "Failed to add test IP. Ensure valid IPv4/IPv6 literal or CIDR format."
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDeleteIp = async (ip: string) => {
    if (!token) return;
    try {
      await api.deleteTestIp(token, ip);
      setConfirmDeleteIp(null);
      setSuccessMsg(`Rule "${ip}" removed.`);
      await fetchConfig();
      onConfigChanged?.();
      setTimeout(() => setSuccessMsg(null), 3000);
    } catch (err: any) {
      setSubmitError(
        err instanceof ApiError ? err.message : "Failed to remove test IP rule."
      );
    }
  };

  const filteredCustomIps = useMemo(() => {
    if (!data?.custom_ips) return [];
    if (!search) return data.custom_ips;
    const needle = search.toLowerCase();
    return data.custom_ips.filter(
      (r) =>
        r.ip_address.toLowerCase().includes(needle) ||
        (r.description && r.description.toLowerCase().includes(needle))
    );
  }, [data?.custom_ips, search]);

  const pagedCustomIps = useMemo(() => {
    const start = (page - 1) * pageSize;
    return filteredCustomIps.slice(start, start + pageSize);
  }, [filteredCustomIps, page, pageSize]);

  const columns: ColumnDef<TestIpRow>[] = [
    {
      key: "ip_address",
      header: "IP / CIDR Rule",
      render: (r) => (
        <span className="font-mono font-medium text-slate-800 dark:text-slate-200 text-xs">
          {r.ip_address}
        </span>
      ),
    },
    {
      key: "description",
      header: "Description & Purpose",
      render: (r) => (
        <span className="text-xs text-slate-600 dark:text-slate-300 line-clamp-1">
          {r.description || "—"}
        </span>
      ),
    },
    {
      key: "created_at",
      header: "Configured At",
      render: (r) => (
        <span className="text-xs text-slate-500 dark:text-slate-400 whitespace-nowrap">
          <RelativeTime value={r.created_at} />
        </span>
      ),
    },
    {
      key: "actions",
      header: "Action",
      align: "right",
      headerAlign: "center",
      render: (r) => (
        <button
          type="button"
          onClick={() => setConfirmDeleteIp(r.ip_address)}
          className="p-1 text-slate-400 dark:text-slate-500 hover:text-rose-600 dark:hover:text-rose-400 transition"
          aria-label={`Delete ${r.ip_address}`}
        >
          <Trash2 className="w-4 h-4" />
        </button>
      ),
    },
  ];

  if (!isOpen) return null;

  const content = (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="test-ip-manager-title"
      className="fixed inset-0 z-100 flex items-center justify-center bg-slate-900/40 backdrop-blur-xs p-4 overflow-y-auto animate-in fade-in duration-200"
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-3xl rounded-2xl bg-white dark:bg-dark-surface shadow-2xl border border-slate-200 dark:border-white/10 overflow-hidden my-8"
      >
        {/* Modal Header */}
        <div className="flex items-center justify-between border-b border-slate-200 dark:border-white/10 px-6 py-4 bg-slate-50 dark:bg-dark-card">
          <div className="flex items-center gap-2">
            <Shield className="w-5 h-5 text-brand-600 dark:text-brand-400" />
            <h2 id="test-ip-manager-title" className="text-base font-semibold text-slate-900 dark:text-white">
              Manage Test IP Rules & Audience Segmentation
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 dark:text-slate-500 hover:bg-slate-200/50 dark:hover:bg-white/10 hover:text-slate-700 dark:hover:text-slate-200 transition"
            aria-label="Close modal"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Modal Body */}
        <div className="p-6 space-y-6 max-h-[75vh] overflow-y-auto">
          {/* Classification note banner */}
          <div className="flex items-start gap-3 rounded-xl border border-teal-100 dark:border-teal-900/60 bg-teal-50/50 dark:bg-teal-950/40 p-4 text-xs text-teal-900 dark:text-teal-200">
            <Info className="w-4 h-4 text-brand-600 dark:text-brand-400 shrink-0 mt-0.5" />
            <div className="space-y-1">
              <p className="font-semibold text-teal-950 dark:text-teal-100">
                Audience Re-classification Guarantee
              </p>
              <p className="text-teal-800 dark:text-teal-300">
                {data?.classification_note ||
                  "Historical telemetry is dynamically re-classified whenever test IPs are updated. All analytics, session threads, and query records immediately reflect new CIDR rules."}
              </p>
            </div>
          </div>

          {/* Add IP Form */}
          <form
            onSubmit={handleAddIp}
            className="rounded-xl border border-slate-200 dark:border-white/10 bg-slate-50/60 dark:bg-dark-card p-4 space-y-3"
          >
            <h3 className="text-xs font-semibold text-slate-800 dark:text-slate-200 uppercase tracking-wider">
              Add New Test IP / CIDR Subnet Rule
            </h3>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <div>
                <label className="block text-[11px] font-medium text-slate-600 dark:text-slate-400 mb-1">
                  IP / CIDR / Prefix *
                </label>
                <input
                  type="text"
                  required
                  maxLength={64}
                  value={newIp}
                  onChange={(e) => setNewIp(e.target.value)}
                  placeholder="e.g. 192.168.1.0/24 or 10.0.0.1"
                  className="w-full rounded-lg border border-slate-300 dark:border-white/10 bg-white dark:bg-dark-surface px-3 py-1.5 text-xs font-mono text-slate-900 dark:text-slate-100 placeholder:text-slate-400 dark:placeholder:text-slate-500 focus:border-brand-600 focus:ring-1 focus:ring-brand-600"
                />
              </div>

              <div className="sm:col-span-2">
                <label className="block text-[11px] font-medium text-slate-600 dark:text-slate-400 mb-1">
                  Description / Team Purpose
                </label>
                <div className="flex gap-2">
                  <input
                    type="text"
                    maxLength={200}
                    value={newDesc}
                    onChange={(e) => setNewDesc(e.target.value)}
                    placeholder="e.g. QA team automated runner or Office VPN"
                    className="flex-1 rounded-lg border border-slate-300 dark:border-white/10 bg-white dark:bg-dark-surface px-3 py-1.5 text-xs text-slate-900 dark:text-slate-100 placeholder:text-slate-400 dark:placeholder:text-slate-500 focus:border-brand-600 focus:ring-1 focus:ring-brand-600"
                  />
                  <button
                    type="submit"
                    disabled={isSubmitting || !newIp.trim()}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-brand-gradient px-4 py-1.5 text-xs font-medium text-white shadow-brand-glow hover:opacity-90 disabled:opacity-50 transition"
                  >
                    {isSubmitting ? (
                      <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      <Plus className="w-3.5 h-3.5" />
                    )}
                    <span>Add Rule</span>
                  </button>
                </div>
              </div>
            </div>

            {submitError && (
              <div className="flex items-center gap-2 rounded-lg bg-rose-50 p-2.5 text-xs text-rose-700 border border-rose-200">
                <AlertTriangle className="w-4 h-4 shrink-0" />
                <span>{submitError}</span>
              </div>
            )}

            {successMsg && (
              <div className="flex items-center gap-2 rounded-lg bg-emerald-50 p-2.5 text-xs text-emerald-700 border border-emerald-200">
                <CheckCircle2 className="w-4 h-4 shrink-0" />
                <span>{successMsg}</span>
              </div>
            )}
          </form>

          {/* Delete confirmation alert */}
          {confirmDeleteIp && (
            <div className="flex items-center justify-between rounded-xl border border-rose-200 bg-rose-50/70 p-3.5 text-xs text-rose-800">
              <div className="flex items-center gap-2">
                <AlertTriangle className="w-4 h-4 text-rose-600 shrink-0" />
                <span>
                  Confirm removal of rule <strong>{confirmDeleteIp}</strong>? History will be
                  re-classified.
                </span>
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setConfirmDeleteIp(null)}
                  className="rounded-md border border-line-strong bg-surface-card px-2.5 py-1 text-xs text-fg-muted hover:bg-surface-muted"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={() => handleDeleteIp(confirmDeleteIp)}
                  className="rounded-md bg-rose-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-rose-700"
                >
                  Delete Rule
                </button>
              </div>
            </div>
          )}

          {/* Custom IPs Table */}
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-xs font-semibold text-slate-800 dark:text-slate-200 uppercase tracking-wider">
                Active Custom Rules ({data?.custom_ips?.length || 0})
              </h3>
            </div>

            <TableToolbar
              search={search}
              onSearchChange={setSearch}
              searchPlaceholder="Filter custom rules by IP or description..."
            />

            <DataTable
              columns={columns}
              data={pagedCustomIps}
              keyField="id"
              isLoading={loading}
              error={error}
              onRetry={fetchConfig}
              emptyMessage="No custom test IP rules configured."
            />

            <Pagination
              page={page}
              pageCount={Math.ceil((filteredCustomIps.length || 0) / pageSize)}
              totalItems={filteredCustomIps.length}
              pageSize={pageSize}
              onPageChange={setPage}
              onPageSizeChange={setPageSize}
            />
          </div>

          {/* Default System IPs Strip */}
          <div className="space-y-2 border-t border-slate-100 dark:border-white/10 pt-4">
            <span className="text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider block">
              Default System Loopbacks & Non-Removable Test Subnets
            </span>
            <div className="flex flex-wrap gap-1.5">
              {(data?.default_ips || ["127.0.0.1", "::1", "localhost"]).map((ip) => (
                <span
                  key={ip}
                  className="rounded-md bg-slate-100 dark:bg-white/5 border border-slate-200 dark:border-white/10 px-2 py-0.5 text-[11px] font-mono text-slate-600 dark:text-slate-300"
                >
                  {ip}
                </span>
              ))}
            </div>
          </div>
        </div>

        {/* Modal Footer */}
        <div className="border-t border-slate-200 dark:border-white/10 px-6 py-3 bg-slate-50 dark:bg-dark-card flex justify-end">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-slate-300 dark:border-white/10 bg-white dark:bg-dark-surface px-4 py-1.5 text-xs font-medium text-slate-700 dark:text-slate-200 hover:bg-slate-100 dark:hover:bg-white/5 transition"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );

  if (typeof document !== "undefined") {
    return createPortal(content, document.body);
  }

  return content;
}
