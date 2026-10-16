import { afterEach, describe, expect, it, vi } from "vitest";
import { checkPrimary, deriveRamp, formatRatio } from "@/lib/brand-palette";
import { normalizeBrand, brandCssVariablesComplete, DEFAULT_BRAND } from "@/lib/brand";
import { SettingsApiError, checkAssetFile, isSafeLink, settingsApi } from "@/lib/settings-api";
import { userCan } from "@/lib/use-can";
import {
  defaultBrandingForm,
  diffBrandingForm,
  formFromBranding,
  validateBrandingForm,
} from "@/components/admin/settings/branding-form";
import { GET as getBrandAsset } from "@/app/branding/assets/[sha]/route";
import { RAMP_008080, RAMP_3355CC, SHA_A, branding } from "../components/settings/harness";

afterEach(() => vi.unstubAllGlobals());

describe("brand palette (client mirror of backend palette.py)", () => {
  it("derives the same 50-950 ramp as the server", () => {
    expect(deriveRamp("#008080")).toEqual(RAMP_008080);
    expect(deriveRamp("#3355CC")).toEqual(RAMP_3355CC);
    expect(deriveRamp("#e4007c")).toMatchObject({ "400": "#fe79aa", "950": "#490024" });
    expect(deriveRamp("teal")).toBeNull();
  });

  it("checks brand-600 on white and brand-400 on the dark background with the server's wording", () => {
    expect(checkPrimary("#008080")).toMatchObject({ passes: true, reason: null });
    const grey = checkPrimary("#777777");
    expect(grey.passes).toBe(false);
    expect(grey.reason).toBe("brand-600 #777777 on white is 4.48:1 (needs 4.5:1); choose a darker primary colour");
    expect(checkPrimary("#ffcc00").reason).toContain("1.51:1");
    expect(checkPrimary("nope")).toMatchObject({ passes: false, checks: [] });
    expect(formatRatio(4.4789)).toBe("4.48:1");
  });
});

describe("settings-api validation helpers", () => {
  it("isSafeLink allows https:// and same-origin paths only", () => {
    for (const ok of ["https://contoso.example/privacy", "/privacy", "/help?x=1"]) expect(isSafeLink(ok)).toBe(true);
    for (const bad of [
      "",
      "http://contoso.example",
      "javascript:alert(1)",
      "data:text/html,x",
      "//evil.example",
      "/a b",
      "/a\\b",
      "https://user:pw@contoso.example",
      "ftp://x",
    ]) {
      expect(isSafeLink(bad)).toBe(false);
    }
  });

  it("checkAssetFile pre-checks type, extension and size", () => {
    expect(checkAssetFile({ name: "a.png", type: "image/png", size: 10 })).toBeNull();
    expect(checkAssetFile({ name: "a.svg", type: "image/svg+xml", size: 10 })).toBeNull();
    expect(checkAssetFile({ name: "a.webp", type: "", size: 10 })).toBeNull();
    expect(checkAssetFile({ name: "a.gif", type: "image/gif", size: 10 })).toMatch(/not a PNG/);
    expect(checkAssetFile({ name: "a.png", type: "image/svg+xml", size: 10 })).toMatch(/not a PNG/);
    expect(checkAssetFile({ name: "a.png", type: "image/png", size: 0 })).toMatch(/empty/);
    expect(checkAssetFile({ name: "a.png", type: "image/png", size: 512 * 1024 })).toBeNull();
    expect(checkAssetFile({ name: "a.png", type: "image/png", size: 512 * 1024 + 1 })).toMatch(/limit is 512 KB/);
  });

  it("surfaces problem+json detail, request id and field errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            detail: "Request validation failed",
            request_id: "req-9",
            errors: [{ loc: ["body", "privacy_url"], msg: "URL must start with https://", type: "value_error" }],
          }),
          { status: 422 },
        ),
      ),
    );
    const error = await settingsApi.updateBranding({ version: 1, privacy_url: "http://x" }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(SettingsApiError);
    expect(error).toMatchObject({
      status: 422,
      requestId: "req-9",
      message: "Request validation failed (privacy_url: URL must start with https://)",
    });
  });

  it("calls the documented routes through the proxy", async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => new Response(JSON.stringify({ items: [] }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await settingsApi.listTaxonomy();
    await settingsApi.updateTaxonomy("bot_quality", "OK", { label: "Fine" });
    await settingsApi.archiveTaxonomy("issue", "KB_GAP");
    await settingsApi.createTaxonomy({ kind: "intent", code: "X", label: "x" });
    const seen = fetchMock.mock.calls.map(([url, init]) => `${init?.method ?? "GET"} ${url}`);
    expect(seen).toEqual([
      "GET /api/proxy/admin/insights/taxonomy?include_archived=true",
      "PATCH /api/proxy/admin/insights/taxonomy/bot_quality/OK",
      "POST /api/proxy/admin/insights/taxonomy/issue/KB_GAP/archive",
      "POST /api/proxy/admin/insights/taxonomy",
    ]);
  });
});

describe("branding form model", () => {
  it("round-trips the server payload and diffs only changes", () => {
    const base = formFromBranding(branding());
    expect(diffBrandingForm(base, base)).toEqual({});
    expect(diffBrandingForm(base, { ...base, primary_hex: "#3355CC", accent_hex: "" })).toEqual({
      primary_hex: "#3355cc",
      accent_hex: null,
    });
  });

  it("validates required names, emails, colours, URLs and questions", () => {
    const form = {
      ...defaultBrandingForm(),
      product_name: " ",
      support_email: "nope",
      access_contact_email: "x@",
      primary_hex: "#12345",
      terms_url: "http://x",
      chat_sample_questions: [],
    };
    expect(validateBrandingForm(form)).toEqual({
      product_name: "Required.",
      support_email: "Enter a valid email address.",
      access_contact_email: "Enter a valid email address, or leave it empty.",
      primary_hex: "Use a #RRGGBB colour.",
      terms_url: "Must start with https:// or be a relative path such as /privacy.",
      chat_sample_questions: "Add at least one sample question.",
    });
    expect(validateBrandingForm(defaultBrandingForm())).toEqual({});
  });
});

describe("normalizeBrand with the PR-08 payload", () => {
  it("reads assets.* URLs, the access contact email and links", () => {
    const brand = normalizeBrand(
      branding({
        assets: { logo_light: `/branding/assets/${SHA_A}`, logo_dark: null, mark_light: null, mark_dark: null, favicon: null },
        access_contact_email: "it@contoso.example",
        terms_url: "javascript:alert(1)",
      }),
    );
    expect(brand.logos.light).toBe(`/branding/assets/${SHA_A}`);
    expect(brand.logos.dark).toBe(DEFAULT_BRAND.logos.dark);
    expect(brand.accessContactEmail).toBe("it@contoso.example");
    expect(brand.links).toEqual({ privacy: "/privacy", terms: null, help: null });
  });

  it("brandCssVariablesComplete writes every shade (defaults included)", () => {
    const vars = brandCssVariablesComplete({ palette: { "600": "#3355cc" } });
    expect(Object.keys(vars)).toHaveLength(11);
    expect(vars["--brand-600"]).toBe("51 85 204");
    expect(vars["--brand-50"]).toBe("234 246 245");
  });
});

describe("userCan (interim permission check until PR-18)", () => {
  it("maps roles like backend ROLE_PERMISSIONS", () => {
    expect(userCan({ role: "superadmin" }, "branding:write")).toBe(true);
    expect(userCan({ role: "admin" }, "branding:write")).toBe(false);
    expect(userCan({ role: "admin" }, "eval:run")).toBe(true);
    expect(userCan({ role: "operator" }, "taxonomy:write")).toBe(false);
    expect(userCan({ role: null }, "dashboard:read")).toBe(false);
    expect(userCan({ role: "hacker" }, "dashboard:read")).toBe(false);
    expect(userCan(null, "dashboard:read")).toBe(false);
  });

  it("prefers an explicit permissions list (GET /auth/me) and keeps the pre-auth guest working", () => {
    expect(userCan({ role: "superadmin", permissions: ["dashboard:read"] }, "branding:write")).toBe(false);
    expect(userCan({ permissions: ["branding:write"] }, "branding:write")).toBe(true);
    expect(userCan({ id: "guest" }, "branding:write")).toBe(true);
  });
});

describe("GET /branding/assets/[sha] passthrough", () => {
  const request = (headers: Record<string, string> = {}) =>
    ({ headers: new Headers(headers) }) as unknown as Parameters<typeof getBrandAsset>[0];

  it("rejects anything that is not a sha256 without calling the backend", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    for (const sha of ["..", "abc", `${SHA_A}/x`, SHA_A.toUpperCase(), "%2e%2e"]) {
      const res = await getBrandAsset(request(), { params: { sha } });
      expect(res.status).toBe(404);
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards GET to the public backend route with If-None-Match and keeps only the safe headers", async () => {
    const fetchMock = vi.fn(async () =>
      new Response("<svg/>", {
        status: 200,
        headers: {
          "Content-Type": "image/svg+xml",
          ETag: `"${SHA_A}"`,
          "Cache-Control": "public, max-age=31536000, immutable",
          "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
          "Set-Cookie": "session=leak",
        },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const res = await getBrandAsset(request({ "if-none-match": '"old"', authorization: "Bearer x" }), { params: { sha: SHA_A } });
    expect(res.status).toBe(200);
    expect(res.headers.get("cache-control")).toBe("public, max-age=31536000, immutable");
    expect(res.headers.get("content-security-policy")).toContain("default-src 'none'");
    expect(res.headers.get("x-content-type-options")).toBe("nosniff");
    expect(res.headers.get("set-cookie")).toBeNull();
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(new RegExp(`/branding/assets/${SHA_A}$`));
    const sent = new Headers(init.headers);
    expect(sent.get("if-none-match")).toBe('"old"');
    expect(sent.get("authorization")).toBeNull();
    expect(sent.get("x-api-key")).toBeNull();
  });

  it("passes 304 and 404 through and maps other failures to 502", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 304, headers: { ETag: `"${SHA_A}"` } })));
    expect((await getBrandAsset(request(), { params: { sha: SHA_A } })).status).toBe(304);
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 404 })));
    expect((await getBrandAsset(request(), { params: { sha: SHA_A } })).status).toBe(404);
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 500 })));
    expect((await getBrandAsset(request(), { params: { sha: SHA_A } })).status).toBe(502);
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new Error("ECONNREFUSED"))));
    expect((await getBrandAsset(request(), { params: { sha: SHA_A } })).status).toBe(502);
  });
});
