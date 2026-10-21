"use client";

import React, { useEffect, useId, useMemo, useRef, useState } from "react";
import { AlertTriangle, Globe2, Link2, Plus, Save, X } from "lucide-react";
import { ErrorState } from "@/components/ui/ErrorState";
import { RelativeTime } from "@/components/ui/RelativeTime";
import { SkeletonCard } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/ToastProvider";
import { focusRing, primaryButton, secondaryButton } from "@/components/ui/styles";
import { errorStatus, queryCache, useApiMutation, useApiQuery } from "@/lib/query-cache";
import {
  KNOWLEDGE_SETTINGS_KEY,
  SettingsApiError,
  settingsApi,
  type KnowledgeSettingsRead,
  type KnowledgeSettingsUpdate,
} from "@/lib/settings-api";
import { MAX_ALLOWED_HOSTS, MAX_HOST_CHARS, normalizeHostEntry } from "@/lib/safe-url";
import { SettingsCard, inputClass } from "./fields";

const HTTP_UNPROCESSABLE = 422;

export const KNOWLEDGE_CONFLICT_TOAST = {
  title: "Knowledge link settings were changed by someone else",
  description: "The latest values have been loaded. Review them and re-apply your changes.",
  tone: "info",
} as const;

interface KnowledgeForm {
  hosts: string[];
  requireUrl: boolean;
}

const sameHosts = (a: readonly string[], b: readonly string[]) => a.length === b.length && a.every((h, i) => h === b[i]);

/** Superadmin Settings > Knowledge links: the source URL domain allow-list and the required-URL switch. */
export function KnowledgeLinksTab() {
  const toast = useToast();
  const hostInputId = useId();
  const switchId = useId();
  const query = useApiQuery<KnowledgeSettingsRead>(KNOWLEDGE_SETTINGS_KEY, (signal) => settingsApi.getKnowledgeSettings(signal), {
    revalidateOnFocus: false,
  });
  const server = query.data;
  const [baseline, setBaseline] = useState<KnowledgeForm | null>(null);
  const [form, setForm] = useState<KnowledgeForm | null>(null);
  const [draft, setDraft] = useState("");
  const [draftError, setDraftError] = useState<string | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);
  const loadedVersion = useRef<number | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Reload on every new server version (first load, save, 409 refetch).
  useEffect(() => {
    if (!server || loadedVersion.current === server.version) return;
    loadedVersion.current = server.version;
    const next = { hosts: [...server.allowed_hosts], requireUrl: server.require_source_url };
    setBaseline(next);
    setForm(next);
  }, [server]);

  const save = useApiMutation<KnowledgeSettingsUpdate, KnowledgeSettingsRead>({
    mutationFn: (body) => settingsApi.updateKnowledgeSettings(body),
    onSuccess: (result) => {
      queryCache.setData<KnowledgeSettingsRead>(KNOWLEDGE_SETTINGS_KEY, result);
      setServerError(null);
      toast.show({ title: "Knowledge link settings saved", tone: "success" });
    },
    onConflict: () => {
      setServerError(null);
      void query.refetch();
      toast.show(KNOWLEDGE_CONFLICT_TOAST);
    },
    onError: (error) => {
      const status = errorStatus(error);
      if (status === 409) return;
      const message = error instanceof Error ? error.message : "Saving failed.";
      setServerError(status === HTTP_UNPROCESSABLE ? message : `Saving failed: ${message}`);
    },
  });

  const maxHosts = server?.max_hosts ?? MAX_ALLOWED_HOSTS;
  const maxHostLength = server?.max_host_length ?? MAX_HOST_CHARS;
  const dirty = useMemo(
    () => Boolean(form && baseline && (form.requireUrl !== baseline.requireUrl || !sameHosts(form.hosts, baseline.hosts))),
    [form, baseline],
  );

  if (query.error && !server) {
    return (
      <ErrorState
        title="Could not load knowledge link settings"
        message={query.error instanceof Error ? query.error.message : undefined}
        requestId={query.error instanceof SettingsApiError ? query.error.requestId : null}
        onRetry={() => void query.refetch()}
      />
    );
  }
  if (!server || !form || !baseline) {
    return (
      <div aria-busy="true">
        <SkeletonCard label="Loading knowledge link settings" className="h-64" />
      </div>
    );
  }

  const full = form.hosts.length >= maxHosts;

  function addHost() {
    if (!form) return;
    if (full) {
      setDraftError(`At most ${maxHosts} domains.`);
      return;
    }
    const check = normalizeHostEntry(draft, maxHostLength);
    if (!check.ok) {
      setDraftError(check.error);
      return;
    }
    if (form.hosts.includes(check.host)) {
      setDraftError(`${check.host} is already on the list.`);
      return;
    }
    setForm({ ...form, hosts: [...form.hosts, check.host] });
    setDraft("");
    setDraftError(null);
    inputRef.current?.focus();
  }

  function removeHost(host: string) {
    setForm((current) => (current ? { ...current, hosts: current.hosts.filter((h) => h !== host) } : current));
    inputRef.current?.focus();
  }

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!dirty || !form || !server) return;
    void save.mutate({ allowed_hosts: form.hosts, require_source_url: form.requireUrl, version: server.version });
  }

  return (
    <form onSubmit={submit} noValidate aria-label="Knowledge link settings" className="space-y-5">
      {serverError ? (
        <div role="alert" className="flex items-start gap-2.5 rounded-xl border border-status-danger/30 bg-status-danger-bg px-3.5 py-3 text-sm text-status-danger-fg">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={1.75} aria-hidden="true" />
          <span>{serverError}</span>
        </div>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-2">
        <SettingsCard
          title="Allowed domains"
          description="Source URLs of knowledge documents must use one of these hosts. A link is never fetched by the server."
          icon={Globe2}
        >
          <div className="space-y-3">
            <div className="space-y-1">
              <label htmlFor={hostInputId} className="block text-xs font-semibold text-fg">
                Add a domain
              </label>
              <div className="flex items-start gap-2">
                <input
                  ref={inputRef}
                  id={hostInputId}
                  type="text"
                  inputMode="url"
                  autoComplete="off"
                  spellCheck={false}
                  maxLength={maxHostLength}
                  value={draft}
                  placeholder="docs.example.com or *.example.com"
                  aria-invalid={Boolean(draftError)}
                  aria-describedby={`${hostInputId}-hint${draftError ? ` ${hostInputId}-error` : ""}`}
                  onChange={(e) => {
                    setDraft(e.target.value);
                    setDraftError(null);
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      addHost();
                    }
                  }}
                  className={`${inputClass} font-mono`}
                />
                <button type="button" onClick={addHost} disabled={!draft.trim()} className={secondaryButton}>
                  <Plus className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                  Add
                </button>
              </div>
              <p id={`${hostInputId}-hint`} className="text-[11px] text-fg-subtle">
                A host name, or one leading wildcard (*.example.com matches its subdomains). Up to {maxHosts} domains.
              </p>
              {draftError ? (
                <p id={`${hostInputId}-error`} role="alert" className="text-[11px] font-medium text-status-danger-fg">
                  {draftError}
                </p>
              ) : null}
            </div>

            {form.hosts.length ? (
              <ul aria-label="Allowed domains" className="flex flex-wrap gap-1.5">
                {form.hosts.map((host) => (
                  <li
                    key={host}
                    className="inline-flex max-w-full items-center gap-1 rounded-full border border-line bg-surface-muted py-0.5 pl-2.5 pr-1 font-mono text-[11px] text-fg"
                  >
                    <span className="truncate" title={host}>
                      {host}
                    </span>
                    <button
                      type="button"
                      onClick={() => removeHost(host)}
                      aria-label={`Remove ${host}`}
                      className={`inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-fg-subtle hover:bg-surface-card hover:text-fg ${focusRing}`}
                    >
                      <X className="h-3 w-3" strokeWidth={2} aria-hidden="true" />
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}
            <p role="note" className="rounded-lg bg-surface-muted px-3 py-2 text-xs text-fg-muted">
              {form.hosts.length
                ? `${form.hosts.length} of ${maxHosts} domains. Documents whose link is on another host show "Link not allowed" and their citations show no link.`
                : "The list is empty: any https domain is allowed."}
            </p>
          </div>
        </SettingsCard>

        <SettingsCard
          title="Upload rule"
          description="Whether a new document needs a source URL before it can be ingested."
          icon={Link2}
        >
          <div className="flex items-start justify-between gap-4">
            <div className="text-sm text-fg">
              <span id={`${switchId}-label`} className="block font-semibold">
                Require a source URL on upload
              </span>
              <span id={`${switchId}-desc`} className="mt-0.5 block text-xs text-fg-muted">
                New documents without a valid URL are rejected. Re-uploads keep their stored link; existing documents
                without one show &quot;Missing link&quot;.
              </span>
            </div>
            <button
              id={switchId}
              type="button"
              role="switch"
              aria-checked={form.requireUrl}
              aria-labelledby={`${switchId}-label`}
              aria-describedby={`${switchId}-desc`}
              onClick={() => setForm({ ...form, requireUrl: !form.requireUrl })}
              className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors motion-reduce:transition-none ${
                form.requireUrl ? "bg-brand-600 dark:bg-brand-500" : "bg-line-strong"
              } ${focusRing}`}
            >
              <span
                aria-hidden="true"
                className={`inline-block h-5 w-5 rounded-full bg-surface-card shadow transition-transform motion-reduce:transition-none ${
                  form.requireUrl ? "translate-x-5" : "translate-x-0.5"
                }`}
              />
            </button>
          </div>
        </SettingsCard>
      </div>

      <div className="sticky bottom-0 z-10 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-line bg-surface-elevated/95 px-4 py-3 shadow-md backdrop-blur">
        <p className="text-[11px] text-fg-subtle">
          {server.updated_at ? (
            <>
              Last changed <RelativeTime value={server.updated_at} />
              {server.updated_by ? ` by ${server.updated_by}` : ""}
            </>
          ) : (
            "Never changed: defaults in use"
          )}
        </p>
        <div className="flex items-center gap-2">
          {dirty ? (
            <button
              type="button"
              className={secondaryButton}
              onClick={() => {
                setForm(baseline);
                setServerError(null);
                setDraftError(null);
              }}
            >
              Discard
            </button>
          ) : null}
          <button type="submit" className={primaryButton} disabled={!dirty || save.isPending}>
            <Save className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            {save.isPending ? "Saving…" : "Save changes"}
          </button>
        </div>
      </div>
    </form>
  );
}
