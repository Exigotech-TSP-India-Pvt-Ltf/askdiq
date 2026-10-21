"use client";

import React, { useState } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { RelativeTime } from "@/components/ui/RelativeTime";
import { InfoTip } from "@/components/ui/InfoTip";
import { primaryButton, secondaryButton } from "@/components/ui/styles";
import { queryCache, useApiMutation, useApiQuery } from "@/lib/query-cache";
import { azureApi, azureKeys, hasDrift, type OfficialPrice, type OfficialPrices } from "@/lib/azure-api";
import type { ModelSettingsRead, ModelSettingsUpdate, PriceField } from "@/lib/settings-api";

const ROLE_LABELS: Record<OfficialPrice["role"], string> = {
  prompt: "LLM input",
  cached: "LLM cached input",
  output: "LLM output",
  embed: "Embeddings",
};

const usd = (value: number) => `$${value.toLocaleString("en-US", { maximumFractionDigits: 6 })}`;
const money = (value: number, currency: string) =>
  `${value.toLocaleString("en-US", { maximumFractionDigits: 6 })} ${currency}`;

function formatDate(value: string | null): string {
  if (!value) return "unknown date";
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleDateString("en-AU", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
}

/** The PUT body that applies every found official price (provenance `azure_retail`). */
export function officialUpdate(version: number, official: OfficialPrices): ModelSettingsUpdate | null {
  const prices = official.prices.filter((p) => p.found && p.usd_per_million !== null);
  if (!prices.length) return null;
  const body: ModelSettingsUpdate = { version, price_source: "azure_retail" };
  for (const p of prices) body[p.field as PriceField] = p.usd_per_million;
  return body;
}

/** Saved prices that differ from the stored official price by more than 1%. */
export function driftedPrices(server: ModelSettingsRead, official: OfficialPrices | undefined): OfficialPrice[] {
  if (!official || !Array.isArray(official.prices)) return [];
  return official.prices.filter((p) => p.found && hasDrift(server[p.field], p.usd_per_million));
}

function SourceLine({ server }: { server: ModelSettingsRead }) {
  if (server.price_source === "azure_retail") {
    return (
      <p className="text-[11px] text-fg-subtle">
        Source: Azure retail, effective {formatDate(server.prices_effective_date ?? null)}, synced{" "}
        <RelativeTime value={server.prices_synced_at ?? null} />
      </p>
    );
  }
  return (
    <p className="inline-flex items-center gap-1 text-[11px] text-fg-subtle">
      Source: {server.price_source === "manual" ? "entered manually" : "the server defaults"}
      {server.price_source === "manual" ? null : <InfoTip label="server defaults">Server defaults come from the backend .env file, set by an operator.</InfoTip>}
    </p>
  );
}

function PreviewTable({ server, official }: { server: ModelSettingsRead; official: OfficialPrices }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <caption className="sr-only">Official Azure retail prices compared with the saved prices</caption>
        <thead>
          <tr className="text-left text-[11px] uppercase tracking-wide text-fg-subtle">
            <th scope="col" className="pb-1 pr-3 font-medium">Rate (per 1M)</th>
            <th scope="col" className="pb-1 pr-3 font-medium">Saved</th>
            <th scope="col" className="pb-1 pr-3 font-medium">Official (USD)</th>
            <th scope="col" className="pb-1 font-medium">{official.local_currency} (reference)</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {official.prices.map((p) => {
            const saved = server[p.field];
            const drift = p.found && hasDrift(saved, p.usd_per_million);
            return (
              <tr key={p.role}>
                <th scope="row" className="py-1.5 pr-3 text-left font-medium text-fg">
                  {ROLE_LABELS[p.role]}
                </th>
                <td className={`py-1.5 pr-3 tabular-nums ${drift ? "text-status-warn-fg" : "text-fg-muted"}`}>{usd(saved)}</td>
                <td className="py-1.5 pr-3 tabular-nums text-fg">
                  {p.found && p.usd_per_million !== null ? usd(p.usd_per_million) : "No official price found"}
                </td>
                <td className="py-1.5 tabular-nums text-fg-muted">
                  {p.local_per_million ? money(p.local_per_million, official.local_currency) : "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

interface OfficialPricesPanelProps {
  server: ModelSettingsRead;
  saving: boolean;
  onConfirm: (body: ModelSettingsUpdate) => Promise<unknown>;
}

/**
 * Settings > Models & Pricing: the official Azure retail prices (stored,
 * refreshed weekly and by "Sync official prices"), a preview against the
 * saved prices, and the drift warning. Nothing changes until the admin
 * confirms; the save goes through the normal model settings update.
 */
export function OfficialPricesPanel({ server, saving, onConfirm }: OfficialPricesPanelProps) {
  const stored = useApiQuery<OfficialPrices>(azureKeys.officialPrices, (signal) => azureApi.officialPrices(signal), {
    staleTime: 60_000,
    revalidateOnFocus: false,
  });
  const [preview, setPreview] = useState<OfficialPrices | null>(null);
  const [error, setError] = useState<string | null>(null);
  const sync = useApiMutation<void, OfficialPrices>({
    mutationFn: () => azureApi.syncOfficialPrices(),
    onSuccess: (result) => {
      queryCache.setData<OfficialPrices>(azureKeys.officialPrices, result);
      setError(null);
      setPreview(result);
    },
    onError: (err) => setError(err instanceof Error ? err.message : "Sync failed."),
  });
  const drifted = driftedPrices(server, stored.data);
  const confirmBody = preview ? officialUpdate(server.version, preview) : null;

  return (
    <div className="space-y-3 border-t border-line pt-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <SourceLine server={server} />
        <button type="button" className={secondaryButton} onClick={() => void sync.mutate()} disabled={sync.isPending}>
          <RefreshCw className={`h-4 w-4 ${sync.isPending ? "animate-spin" : ""}`} strokeWidth={1.75} aria-hidden="true" />
          {sync.isPending ? "Fetching official prices…" : "Sync official prices"}
        </button>
      </div>

      {error ? (
        <p role="alert" className="text-xs text-status-danger-fg">
          {error}
        </p>
      ) : null}

      {drifted.length && !preview ? (
        <div role="status" className="flex items-start gap-2 rounded-lg border border-status-warn/30 bg-status-warn-bg px-3 py-2 text-xs text-status-warn-fg">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={1.75} aria-hidden="true" />
          <span>
            Saved prices differ from the official Azure retail price by more than 1%:{" "}
            {drifted
              .map((p) => `${ROLE_LABELS[p.role]} ${usd(server[p.field])} vs ${usd(p.usd_per_million ?? 0)}`)
              .join("; ")}
            .
          </span>
        </div>
      ) : null}

      {preview ? (
        <div role="region" aria-label="Official price preview" className="space-y-3 rounded-lg border border-line bg-surface-muted/40 p-3">
          <p className="text-xs text-fg-muted">
            Official Azure retail list prices ({preview.deployment_type}, {preview.region}), effective{" "}
            {formatDate(preview.effective_date)}. Retail prices exclude negotiated discounts; USD is authoritative.
          </p>
          <PreviewTable server={server} official={preview} />
          <div className="flex flex-wrap justify-end gap-2">
            <button type="button" className={secondaryButton} onClick={() => setPreview(null)}>
              Cancel
            </button>
            <button
              type="button"
              className={primaryButton}
              disabled={!confirmBody || saving}
              onClick={() => {
                if (!confirmBody) return;
                void onConfirm(confirmBody).then((saved) => {
                  if (saved) setPreview(null);
                });
              }}
            >
              {saving ? "Saving…" : "Use official prices"}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
