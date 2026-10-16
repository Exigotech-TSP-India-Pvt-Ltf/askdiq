"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, m } from "motion/react";
import { AlertTriangle, Building2, Image as ImageIcon, Link2, LogIn, MessageSquareText, Palette, RotateCcw, Save } from "lucide-react";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { ErrorState } from "@/components/ui/ErrorState";
import { SkeletonCard } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/ToastProvider";
import { primaryButton, secondaryButton } from "@/components/ui/styles";
import { DEFAULT_BRAND, normalizeBrand } from "@/lib/brand";
import { useBrandUpdater } from "@/lib/brand-context";
import { MIN_TEXT_CONTRAST, MIN_UI_CONTRAST, checkPrimary, deriveRamp, normalizeHex, safeContrast } from "@/lib/brand-palette";
import { fadeUp, pop, useMotionVariants } from "@/lib/motion";
import { errorStatus, queryCache, useApiMutation, useApiQuery } from "@/lib/query-cache";
import {
  BRANDING_KEY,
  BRAND_ASSET_SLOTS,
  SettingsApiError,
  assetField,
  settingsApi,
  type BrandAssetSlot,
  type BrandAssetUrls,
  type BrandingRead,
  type BrandingUpdate,
} from "@/lib/settings-api";
import { semanticTokens } from "@/lib/theme-tokens";
import { AssetDropzone } from "./AssetDropzone";
import { BrandPreview } from "./BrandPreview";
import { ColorField } from "./ColorField";
import { QuestionsEditor } from "./QuestionsEditor";
import {
  TEXT_LIMITS,
  assetUrlsFromBranding,
  brandFromForm,
  defaultBrandingForm,
  formFromDefaults,
  diffBrandingForm,
  formFromBranding,
  fullBrandingPayload,
  validateBrandingForm,
  type BrandingForm,
} from "./branding-form";
import { ContrastBadge, Field, SettingsCard, inputClass } from "./fields";

const HTTP_UNPROCESSABLE = 422;

const ASSET_LABELS: Record<BrandAssetSlot, { label: string; tone: "light" | "dark"; fallback: string }> = {
  logo_light: { label: "Logo (light)", tone: "light", fallback: DEFAULT_BRAND.logos.light },
  logo_dark: { label: "Logo (dark)", tone: "dark", fallback: DEFAULT_BRAND.logos.dark },
  mark_light: { label: "Mark (light)", tone: "light", fallback: DEFAULT_BRAND.logos.markLight },
  mark_dark: { label: "Mark (dark)", tone: "dark", fallback: DEFAULT_BRAND.logos.markDark },
  favicon: { label: "Favicon", tone: "light", fallback: DEFAULT_BRAND.favicon },
};

export const BRANDING_CONFLICT_TOAST = {
  title: "Branding was changed by someone else",
  description: "The latest version has been loaded. Review it and re-apply your changes.",
  tone: "info",
} as const;

type TextField = Exclude<keyof BrandingForm, "chat_sample_questions" | `${BrandAssetSlot}_asset_id`>;

interface SaveVars {
  body: BrandingUpdate;
  reset?: boolean;
}

export function BrandingTab() {
  const toast = useToast();
  const { setBrand } = useBrandUpdater();
  const query = useApiQuery<BrandingRead>(BRANDING_KEY, (signal) => settingsApi.getBranding(signal), { revalidateOnFocus: false });
  const server = query.data;

  const [baseline, setBaseline] = useState<BrandingForm | null>(null);
  const [form, setForm] = useState<BrandingForm | null>(null);
  const [assetUrls, setAssetUrls] = useState<BrandAssetUrls | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);
  const [assetErrors, setAssetErrors] = useState<Partial<Record<BrandAssetSlot, string>>>({});
  const [uploading, setUploading] = useState<Partial<Record<BrandAssetSlot, boolean>>>({});
  const [confirmReset, setConfirmReset] = useState(false);
  const [showErrors, setShowErrors] = useState(false);
  const loadedVersion = useRef<number | null>(null);
  const popVariants = useMotionVariants(pop);
  const fadeVariants = useMotionVariants(fadeUp);

  // (Re)load the form whenever a new server version arrives: first load, after
  // a save, or after a 409 refetch (the edits are dropped on purpose so a
  // stale value never overwrites someone else's change).
  useEffect(() => {
    if (!server || loadedVersion.current === server.version) return;
    loadedVersion.current = server.version;
    const next = formFromBranding(server);
    setBaseline(next);
    setForm(next);
    setAssetUrls(assetUrlsFromBranding(server));
    setAssetErrors({});
  }, [server]);

  const save = useApiMutation<SaveVars, BrandingRead>({
    mutationFn: ({ body }) => settingsApi.updateBranding(body),
    onSuccess: (result, vars) => {
      queryCache.setData<BrandingRead>(BRANDING_KEY, result);
      setBrand(normalizeBrand(result));
      setServerError(null);
      setShowErrors(false);
      toast.show({
        title: vars.reset ? "Branding reset to defaults" : "Branding saved",
        // Other users' pages and browsers cache branding for up to 5 minutes.
        description: "You see it now. Other users see it within about 5 minutes, or when they reload after that.",
        tone: "success",
      });
    },
    onConflict: () => {
      setServerError(null);
      void query.refetch();
      toast.show(BRANDING_CONFLICT_TOAST);
    },
    onError: (error) => {
      const status = errorStatus(error);
      if (status === 409) return;
      const message = error instanceof Error ? error.message : "Saving failed.";
      setServerError(status === HTTP_UNPROCESSABLE ? message : `Saving failed: ${message}`);
    },
  });

  const errors = useMemo(() => (form ? validateBrandingForm(form) : {}), [form]);
  const changes = useMemo(() => (form && baseline ? diffBrandingForm(baseline, form) : {}), [form, baseline]);
  const dirty = Object.keys(changes).length > 0;
  const valid = Object.keys(errors).length === 0;
  const preview = useMemo(() => (form && assetUrls ? brandFromForm(form, assetUrls) : DEFAULT_BRAND), [form, assetUrls]);
  const primaryCheck = useMemo(() => checkPrimary(form?.primary_hex ?? ""), [form?.primary_hex]);
  const derived = useMemo(() => deriveRamp(form?.primary_hex ?? ""), [form?.primary_hex]);

  if (query.error && !server) {
    return (
      <ErrorState
        title="Could not load branding"
        message={query.error instanceof Error ? query.error.message : undefined}
        requestId={query.error instanceof SettingsApiError ? query.error.requestId : null}
        onRetry={() => void query.refetch()}
      />
    );
  }
  if (!form || !baseline || !assetUrls || !server) {
    return (
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,0.9fr)]" aria-busy="true">
        <div className="space-y-4">
          <SkeletonCard label="Loading branding" className="h-48" />
          <SkeletonCard className="h-48" />
        </div>
        <SkeletonCard className="h-96" />
      </div>
    );
  }

  const set = <K extends keyof BrandingForm>(key: K, value: BrandingForm[K]) =>
    setForm((current) => (current ? { ...current, [key]: value } : current));
  const fieldError = (key: keyof BrandingForm) => (showErrors || form[key] !== baseline[key] ? errors[key] : undefined);

  const text = (key: TextField, label: string, opts: { hint?: string; type?: string; multiline?: boolean } = {}) => (
    <Field label={label} hint={opts.hint} error={fieldError(key)}>
      {(props) =>
        opts.multiline ? (
          <textarea
            {...props}
            rows={3}
            value={form[key]}
            maxLength={TEXT_LIMITS[key as keyof typeof TEXT_LIMITS]}
            onChange={(e) => set(key, e.target.value)}
            className={`${inputClass} resize-y`}
          />
        ) : (
          <input
            {...props}
            type={opts.type ?? "text"}
            value={form[key]}
            maxLength={TEXT_LIMITS[key as keyof typeof TEXT_LIMITS] ?? 2048}
            onChange={(e) => set(key, e.target.value)}
            className={inputClass}
          />
        )
      }
    </Field>
  );

  async function upload(slot: BrandAssetSlot, file: File) {
    setUploading((u) => ({ ...u, [slot]: true }));
    setAssetErrors((e) => ({ ...e, [slot]: undefined }));
    try {
      const asset = await settingsApi.uploadBrandAsset(file);
      set(assetField(slot), asset.id);
      setAssetUrls((urls) => (urls ? { ...urls, [slot]: asset.url } : urls));
    } catch (error) {
      setAssetErrors((e) => ({ ...e, [slot]: error instanceof Error ? error.message : "Upload failed." }));
    } finally {
      setUploading((u) => ({ ...u, [slot]: false }));
    }
  }

  function submit(event: React.FormEvent) {
    event.preventDefault();
    setShowErrors(true);
    if (!valid || !dirty || !server) return;
    void save.mutate({ body: { version: server.version, ...changes } });
  }

  async function resetToDefaults() {
    if (!server) return;
    // The seeded row (login copy, accent, tint, BRAND_* names) comes from the
    // server; the built-in form is only a fallback for an older backend.
    const defaults = await settingsApi
      .getBrandingDefaults()
      .then(formFromDefaults)
      .catch(() => defaultBrandingForm());
    await save.mutate({ body: { version: server.version, ...fullBrandingPayload(defaults) }, reset: true });
    setConfirmReset(false);
  }

  const accentFallback = derived?.["800"] ?? server.accent_hex;
  const tintFallback = derived?.["50"] ?? server.tint_hex;
  const accentRatio = safeContrast(form.accent_hex || accentFallback, semanticTokens.light["surface-card"]);
  const tintRatio = safeContrast(semanticTokens.light.fg, form.tint_hex || tintFallback);

  return (
    <form onSubmit={submit} noValidate aria-label="Branding settings">
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,0.9fr)]">
        <m.div className="space-y-5" variants={fadeVariants} initial="hidden" animate="visible">
          <AnimatePresence>
            {serverError ? (
              <m.div
                key="server-error"
                role="alert"
                variants={popVariants}
                initial="hidden"
                animate="visible"
                exit="exit"
                data-testid="branding-server-error"
                className="flex items-start gap-2 rounded-xl border border-status-danger/40 bg-status-danger-bg px-4 py-3 text-xs text-status-danger-fg"
              >
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={1.75} aria-hidden="true" />
                <span>{serverError}</span>
              </m.div>
            ) : null}
          </AnimatePresence>

          <div data-tour="settings-identity">
          <SettingsCard title="Identity" description="Names shown across the console, login page and chat." icon={Building2}>
            <div className="grid gap-4 sm:grid-cols-2">
              {text("product_name", "Product name")}
              {text("assistant_name", "Assistant name")}
              {text("company_name", "Company name")}
              {text("support_email", "Support email", { type: "email" })}
              {text("access_contact_email", "Access contact email", {
                type: "email",
                hint: "Shown on the login and Access Required pages. Empty uses the support email.",
              })}
            </div>
          </SettingsCard>
          </div>

          <div data-tour="settings-colours">
          <SettingsCard title="Colours" description="Live WCAG check; the server re-checks on save." icon={Palette}>
            <div className="grid gap-4 sm:grid-cols-3">
              <ColorField
                label="Primary"
                value={form.primary_hex}
                onChange={(v) => set("primary_hex", v)}
                fallback={server.primary_hex}
                error={fieldError("primary_hex")}
                hint="Derives the 50 to 950 ramp."
                badges={primaryCheck.checks.map((c) => (
                  <ContrastBadge key={c.id} ratio={c.ratio} min={MIN_TEXT_CONTRAST} label={c.id === "light" ? "Light" : "Dark"} />
                ))}
              />
              <ColorField
                label="Accent"
                value={form.accent_hex}
                onChange={(v) => set("accent_hex", v)}
                fallback={accentFallback}
                error={fieldError("accent_hex")}
                clearable
                badges={<ContrastBadge ratio={accentRatio} min={MIN_UI_CONTRAST} label="On white" />}
              />
              <ColorField
                label="Tint"
                value={form.tint_hex}
                onChange={(v) => set("tint_hex", v)}
                fallback={tintFallback}
                error={fieldError("tint_hex")}
                clearable
                badges={<ContrastBadge ratio={tintRatio} min={MIN_TEXT_CONTRAST} label="Text on tint" />}
              />
            </div>
            {primaryCheck.reason && normalizeHex(form.primary_hex) ? (
              <p className="mt-3 rounded-lg bg-status-warn-bg px-3 py-2 text-[11px] text-status-warn-fg" data-testid="contrast-warning" data-tour="settings-contrast-warning">
                {primaryCheck.reason}. Saving will be rejected.
              </p>
            ) : null}
            {derived ? (
              <div className="mt-3 flex overflow-hidden rounded-lg border border-line" aria-label="Derived brand ramp" role="img">
                {Object.entries(derived).map(([shade, hex]) => (
                  <span key={shade} title={`brand-${shade} ${hex}`} className="h-5 flex-1" style={{ backgroundColor: hex }} />
                ))}
              </div>
            ) : null}
          </SettingsCard>
          </div>

          <div data-tour="settings-logos">
          <SettingsCard title="Logos" description="Drag and drop, or choose a file. Dark artwork is shown on dark surfaces." icon={ImageIcon}>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {BRAND_ASSET_SLOTS.map((slot) => {
                const meta = ASSET_LABELS[slot];
                const id = form[assetField(slot)];
                return (
                  <AssetDropzone
                    key={slot}
                    label={meta.label}
                    tone={meta.tone}
                    src={(id && assetUrls[slot]) || meta.fallback}
                    custom={Boolean(id)}
                    busy={uploading[slot]}
                    error={assetErrors[slot]}
                    onFile={(file) => void upload(slot, file)}
                    onReject={(message) => setAssetErrors((e) => ({ ...e, [slot]: message }))}
                    onClear={() => {
                      set(assetField(slot), null);
                      setAssetErrors((e) => ({ ...e, [slot]: undefined }));
                    }}
                  />
                );
              })}
            </div>
          </SettingsCard>
          </div>

          <div data-tour="settings-login-copy">
          <SettingsCard title="Login page" description="Copy for the split-screen sign-in page." icon={LogIn}>
            <div className="grid gap-4 sm:grid-cols-2">
              {text("login_eyebrow", "Eyebrow", { hint: "Small uppercase label, e.g. the product name." })}
              {text("login_headline", "Headline")}
              <div className="sm:col-span-2">{text("login_subtext", "Supporting copy", { multiline: true })}</div>
              <div className="sm:col-span-2">{text("login_description", "Card description", { multiline: true })}</div>
            </div>
          </SettingsCard>
          </div>

          <div data-tour="settings-links">
          <SettingsCard title="Links" description="https:// or a relative path such as /privacy. Empty links are hidden." icon={Link2}>
            <div className="grid gap-4 sm:grid-cols-3">
              {text("privacy_url", "Privacy policy URL", { type: "url" })}
              {text("terms_url", "Terms of service URL", { type: "url" })}
              {text("help_url", "Help URL", { type: "url" })}
            </div>
          </SettingsCard>
          </div>

          <div data-tour="settings-sample-questions">
          <SettingsCard title="Chat sample questions" description="Suggested prompts on an empty chat (1 to 8)." icon={MessageSquareText}>
            <QuestionsEditor
              value={form.chat_sample_questions}
              onChange={(v) => set("chat_sample_questions", v)}
              error={showErrors || form.chat_sample_questions !== baseline.chat_sample_questions ? errors.chat_sample_questions : undefined}
            />
          </SettingsCard>
          </div>
        </m.div>

        <aside className="space-y-3 xl:sticky xl:top-4 xl:self-start" aria-label="Live preview" data-tour="settings-preview">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-bold text-fg">Live preview</h2>
            <span className="text-[11px] text-fg-subtle">{dirty ? "Unsaved changes" : `Version ${server.version}`}</span>
          </div>
          <BrandPreview brand={preview} />
        </aside>
      </div>

      <div data-tour="settings-save-bar" className="sticky bottom-0 z-10 mt-5 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-line bg-surface-elevated/95 px-4 py-3 shadow-md backdrop-blur">
        <button type="button" className={secondaryButton} onClick={() => setConfirmReset(true)} disabled={save.isPending} data-tour="settings-reset">
          <RotateCcw className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
          Reset to defaults
        </button>
        <div className="flex items-center gap-2">
          {dirty ? (
            <button
              type="button"
              className={secondaryButton}
              onClick={() => {
                setForm(baseline);
                setAssetUrls(assetUrlsFromBranding(server));
                setServerError(null);
                setShowErrors(false);
              }}
            >
              Discard
            </button>
          ) : null}
          <button type="submit" className={primaryButton} disabled={!dirty || save.isPending} aria-disabled={!valid}>
            <Save className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            {save.isPending ? "Saving…" : "Save changes"}
          </button>
        </div>
      </div>

      <ConfirmDialog
        open={confirmReset}
        title="Reset branding to defaults?"
        description="Names, colours, logos, login copy, links and sample questions go back to the seeded defaults for everyone. Uploaded files stay stored."
        confirmLabel="Reset to defaults"
        tone="danger"
        onConfirm={resetToDefaults}
        onCancel={() => setConfirmReset(false)}
      />
    </form>
  );
}
