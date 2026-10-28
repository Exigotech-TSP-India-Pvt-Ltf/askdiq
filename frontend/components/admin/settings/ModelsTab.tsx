"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, BrainCircuit, CircleDollarSign, Database, RotateCcw, Save } from "lucide-react";
import { ErrorState } from "@/components/ui/ErrorState";
import { RelativeTime } from "@/components/ui/RelativeTime";
import { InfoTip } from "@/components/ui/InfoTip";
import { SkeletonCard } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/ToastProvider";
import { focusRing, primaryButton, secondaryButton } from "@/components/ui/styles";
import { errorStatus, queryCache, useApiMutation, useApiQuery } from "@/lib/query-cache";
import {
  MODELS_KEY,
  SettingsApiError,
  settingsApi,
  type ModelSettingsField,
  type ModelSettingsRead,
  type ModelSettingsUpdate,
  type PriceField,
} from "@/lib/settings-api";
import { Field, SettingsCard, inputClass } from "./fields";
import { OfficialPricesPanel } from "./OfficialPrices";
import { diffModelsForm, formFromModelSettings, validateModelsForm, type ModelsForm } from "./models-form";

const HTTP_UNPROCESSABLE = 422;

export const MODELS_CONFLICT_TOAST = {
  title: "Model settings were changed by someone else",
  description: "The latest values have been loaded. Review them and re-apply your changes.",
  tone: "info",
} as const;

const PRICE_LABELS: Record<PriceField, { label: string; hint: string }> = {
  price_prompt_per_million: { label: "LLM input", hint: "Uncached prompt tokens" },
  price_cached_per_million: { label: "LLM cached input", hint: "Prompt tokens served from the cache" },
  price_output_per_million: { label: "LLM output", hint: "Visible and reasoning tokens" },
  price_embed_per_million: { label: "Embeddings", hint: "Query, guardrail and ingestion embeddings" },
};

const usd = (value: number) => `$${value.toLocaleString("en-US", { maximumFractionDigits: 6 })}`;

/** Superadmin Settings > Models: LLM / embedding deployments and per-token prices. */
export function ModelsTab() {
  const toast = useToast();
  const query = useApiQuery<ModelSettingsRead>(MODELS_KEY, (signal) => settingsApi.getModelSettings(signal), {
    revalidateOnFocus: false,
  });
  const server = query.data;
  const [baseline, setBaseline] = useState<ModelsForm | null>(null);
  const [form, setForm] = useState<ModelsForm | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);
  const loadedVersion = useRef<number | null>(null);

  // Reload on every new server version (first load, save, 409 refetch).
  useEffect(() => {
    if (!server || loadedVersion.current === server.version) return;
    loadedVersion.current = server.version;
    const next = formFromModelSettings(server);
    setBaseline(next);
    setForm(next);
  }, [server]);

  const save = useApiMutation<ModelSettingsUpdate, ModelSettingsRead>({
    mutationFn: (body) => settingsApi.updateModelSettings(body),
    onSuccess: (result) => {
      queryCache.setData<ModelSettingsRead>(MODELS_KEY, result);
      setServerError(null);
      toast.show({
        title: "Model settings saved",
        description: "New questions use them right away; other servers pick them up within a minute.",
        tone: "success",
      });
    },
    onConflict: () => {
      setServerError(null);
      void query.refetch();
      toast.show(MODELS_CONFLICT_TOAST);
    },
    onError: (error) => {
      const status = errorStatus(error);
      if (status === 409) return;
      const message = error instanceof Error ? error.message : "Saving failed.";
      setServerError(status === HTTP_UNPROCESSABLE ? message : `Saving failed: ${message}`);
    },
  });

  const errors = useMemo(() => (form ? validateModelsForm(form) : {}), [form]);
  const changes = useMemo(() => (form && baseline ? diffModelsForm(baseline, form) : {}), [form, baseline]);
  const dirty = Object.keys(changes).length > 0;
  const valid = Object.keys(errors).length === 0;

  if (query.error && !server) {
    return (
      <ErrorState
        title="Could not load model settings"
        message={query.error instanceof Error ? query.error.message : undefined}
        requestId={query.error instanceof SettingsApiError ? query.error.requestId : null}
        onRetry={() => void query.refetch()}
      />
    );
  }
  if (!server || !form || !baseline) {
    return (
      <div className="grid gap-4 lg:grid-cols-2" aria-busy="true">
        <SkeletonCard label="Loading model settings" className="h-64" />
        <SkeletonCard className="h-64" />
      </div>
    );
  }

  const set = (field: ModelSettingsField, value: string) => setForm((current) => (current ? { ...current, [field]: value } : current));
  const embeddingChanged = "embedding_model" in changes;

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!dirty || !valid || !server) return;
    void save.mutate({ version: server.version, ...changes });
  }

  const overrideField = (
    field: ModelSettingsField,
    label: string,
    opts: { hint: string; defaultValue: string; type?: "text" | "number"; prefix?: string },
  ) => (
    <Field label={label} hint={form[field] ? opts.hint : `${opts.hint}. Using the server default: ${opts.defaultValue}`} error={errors[field]}>
      {(props) => (
        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            {opts.prefix ? (
              <span className="pointer-events-none absolute inset-y-0 left-3 flex items-center text-sm text-fg-subtle">{opts.prefix}</span>
            ) : null}
            <input
              {...props}
              type={opts.type ?? "text"}
              inputMode={opts.type === "number" ? "decimal" : undefined}
              step={opts.type === "number" ? "any" : undefined}
              min={opts.type === "number" ? 0 : undefined}
              value={form[field]}
              placeholder={opts.defaultValue}
              onChange={(e) => set(field, e.target.value)}
              className={`${inputClass} ${opts.prefix ? "pl-7" : ""} ${opts.type === "number" ? "tabular-nums" : "font-mono"}`}
            />
          </div>
          {form[field] ? (
            <button
              type="button"
              onClick={() => set(field, "")}
              title="Use the server default"
              aria-label={`Use the server default for ${label}`}
              className={`rounded-md p-2 text-fg-subtle transition-colors hover:bg-surface-muted hover:text-fg ${focusRing}`}
            >
              <RotateCcw className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            </button>
          ) : null}
        </div>
      )}
    </Field>
  );

  return (
    <form onSubmit={submit} noValidate aria-label="Model settings" className="space-y-5">
      {serverError ? (
        <div role="alert" className="flex items-start gap-2.5 rounded-xl border border-status-danger/30 bg-status-danger-bg px-3.5 py-3 text-sm text-status-danger-fg">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={1.75} aria-hidden="true" />
          <span>{serverError}</span>
        </div>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-2">
        <SettingsCard
          title="Models"
          description="Azure OpenAI deployment names. A changed model is test-called once before it is saved."
          icon={BrainCircuit}
        >
          <div className="space-y-4">
            {overrideField("llm_deployment", "LLM deployment", {
              hint: "Answers, insights and evaluation judging",
              defaultValue: server.defaults.llm_deployment,
            })}
            {overrideField("embedding_model", "Embedding deployment", {
              hint: `Must return ${server.embedding_dim}-dimension vectors`,
              defaultValue: server.defaults.embedding_model,
            })}
            <div className="flex items-center gap-2 rounded-lg bg-surface-muted px-3 py-2 text-xs text-fg-muted">
              <Database className="h-3.5 w-3.5 shrink-0" strokeWidth={1.75} aria-hidden="true" />
              <span>
                Knowledge vectors: <span className="font-semibold tabular-nums text-fg">{server.embedding_dim}</span> dimensions (EMBEDDING_DIM, fixed)
              </span>
            </div>
            {embeddingChanged ? (
              <div role="status" className="flex items-start gap-2 rounded-lg border border-status-warn/30 bg-status-warn-bg px-3 py-2 text-xs text-status-warn-fg">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={1.75} aria-hidden="true" />
                <span>
                  Vectors from different embedding models do not match. Re-upload your Knowledge documents after this change so
                  retrieval keeps working.
                </span>
              </div>
            ) : null}
          </div>
        </SettingsCard>

        <SettingsCard
          title="Pricing"
          description="USD per 1M tokens. New rates apply to questions asked from now on; past costs keep the rates they were billed at."
          icon={CircleDollarSign}
        >
          <div className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2">
              {(Object.keys(PRICE_LABELS) as PriceField[]).map((field) => (
                <React.Fragment key={field}>
                  {overrideField(field, PRICE_LABELS[field].label, {
                    hint: PRICE_LABELS[field].hint,
                    defaultValue: usd(server.defaults[field]),
                    type: "number",
                    prefix: "$",
                  })}
                </React.Fragment>
              ))}
            </div>
            <OfficialPricesPanel server={server} saving={save.isPending} onConfirm={(body) => save.mutate(body)} />
          </div>
        </SettingsCard>
      </div>

      <div className="sticky bottom-0 z-10 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-line bg-surface-elevated/95 px-4 py-3 shadow-md backdrop-blur-sm">
        <p className="text-[11px] text-fg-subtle">
          {server.updated_at ? (
            <>
              Last changed <RelativeTime value={server.updated_at} />
              {server.updated_by ? ` by ${server.updated_by}` : ""}
            </>
          ) : (
            // Plain wording for admins; the .env detail is in the tip for operators (VA-40).
            <span className="inline-flex items-center gap-1">
              Using the server defaults
              <InfoTip label="server defaults">Server defaults come from the backend .env file, set by an operator.</InfoTip>
            </span>
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
              }}
            >
              Discard
            </button>
          ) : null}
          <button type="submit" className={primaryButton} disabled={!dirty || !valid || save.isPending}>
            <Save className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            {save.isPending ? "Verifying and saving…" : "Save changes"}
          </button>
        </div>
      </div>
    </form>
  );
}
