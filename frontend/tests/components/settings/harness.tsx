/**
 * Shared fixtures and a fetch router for the Settings tests (F7). Payloads
 * mirror backend/app/schemas/branding.py and backend/app/schemas/insights.py.
 */
import React from "react";
import { vi } from "vitest";
import { ThemeProvider } from "@/lib/theme-context";
import { BrandProvider } from "@/lib/brand-context";
import { MotionProvider } from "@/components/providers/MotionProvider";
import { ToastProvider } from "@/components/ui/ToastProvider";
import { queryCache } from "@/lib/query-cache";
import type { BrandingRead, TaxonomyRead } from "@/lib/settings-api";

/** Server ramps (backend palette.derive_ramp) for the colours used in tests. */
export const RAMP_008080 = {
  "50": "#e3f8f7",
  "100": "#d3eded",
  "200": "#b5dad9",
  "300": "#93c4c3",
  "400": "#70afae",
  "500": "#479797",
  "600": "#008080",
  "700": "#006969",
  "800": "#005050",
  "900": "#003b3b",
  "950": "#002929",
};
export const RAMP_3355CC = {
  "50": "#ecf2ff",
  "100": "#dae5ff",
  "200": "#b8cdff",
  "300": "#95b1f7",
  "400": "#7595ea",
  "500": "#5376dc",
  "600": "#3355cc",
  "700": "#2643af",
  "800": "#193091",
  "900": "#0f2076",
  "950": "#07105f",
};

export const SHA_A = "a".repeat(64);

export function branding(overrides: Partial<BrandingRead> = {}): BrandingRead {
  return {
    brand_name: "DeployIQ",
    brand_assistant_name: "AskDIQ",
    brand_support_email: "support@deployiq.com",
    product_name: "DeployIQ",
    assistant_name: "AskDIQ",
    company_name: "Exigo Tech",
    support_email: "support@deployiq.com",
    access_contact_email: null,
    primary_hex: "#008080",
    accent_hex: "#005050",
    tint_hex: "#e3f8f7",
    palette: RAMP_008080,
    logo_light_asset_id: null,
    logo_dark_asset_id: null,
    mark_light_asset_id: null,
    mark_dark_asset_id: null,
    favicon_asset_id: null,
    assets: { logo_light: null, logo_dark: null, mark_light: null, mark_dark: null, favicon: null },
    login_eyebrow: "ASKDIQ",
    login_headline: "Governed answers",
    login_subtext: null,
    login_description: null,
    privacy_url: "/privacy",
    terms_url: null,
    help_url: null,
    chat_sample_questions: ["What is the Essential Eight?", "How is data handled?"],
    version: 3,
    updated_at: null,
    ...overrides,
  };
}

let nextId = 1;
export function entry(overrides: Partial<TaxonomyRead> & Pick<TaxonomyRead, "kind" | "code" | "label">): TaxonomyRead {
  return {
    id: nextId++,
    description: null,
    color: null,
    group: null,
    substantive: true,
    handled: false,
    sort_order: 0,
    is_active: true,
    is_system: true,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    ...overrides,
  };
}

export function taxonomyFixture(): TaxonomyRead[] {
  nextId = 1;
  return [
    entry({ kind: "intent", code: "SVC", label: "Platform and services", group: "Customer / prospect", sort_order: 10 }),
    entry({ kind: "intent", code: "SEC", label: "Security and compliance", group: "Customer / prospect", sort_order: 20 }),
    entry({ kind: "intent", code: "OOS", label: "Off-scope", group: "Off-scope request", sort_order: 30 }),
    entry({ kind: "intent", code: "LEGACY", label: "Legacy product", sort_order: 40, is_active: false, is_system: false }),
    entry({ kind: "bot_quality", code: "OK", label: "Answered correctly", color: "#008080", sort_order: 10, handled: true }),
    entry({ kind: "issue", code: "KB_GAP", label: "Info not in knowledge base", sort_order: 10 }),
  ];
}

export interface Call {
  method: string;
  url: string;
  body: unknown;
}

type Handler = (call: Call) => Response | Promise<Response> | undefined;

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": status >= 400 ? "application/problem+json" : "application/json" },
  });
}

export function problem(status: number, detail: string, extra: Record<string, unknown> = {}): Response {
  return json({ type: "about:blank", title: "Error", status, detail, request_id: "req-123", ...extra }, status);
}

/** Stubs `fetch`; `handler` answers by method + URL, unknown calls fail the test with a 599. */
export function installFetch(handler: Handler) {
  const calls: Call[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const method = (init.method ?? "GET").toUpperCase();
    let body: unknown = init.body;
    if (typeof init.body === "string") {
      try {
        body = JSON.parse(init.body);
      } catch {
        body = init.body;
      }
    }
    const call = { method, url, body };
    calls.push(call);
    return (await handler(call)) ?? problem(599, `Unhandled ${method} ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}

export function resetSettingsState() {
  queryCache.clear();
  document.documentElement.removeAttribute("style");
  document.documentElement.classList.remove("dark");
  window.localStorage.clear();
}

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <ThemeProvider>
      <BrandProvider>
        <MotionProvider>
          <ToastProvider>{children}</ToastProvider>
        </MotionProvider>
      </BrandProvider>
    </ThemeProvider>
  );
}

export function file(name: string, type: string, size: number): File {
  const f = new File(["x"], name, { type });
  Object.defineProperty(f, "size", { value: size });
  return f;
}
