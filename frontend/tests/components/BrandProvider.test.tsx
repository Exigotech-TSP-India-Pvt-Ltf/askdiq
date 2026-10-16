import React from "react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  DEFAULT_BRAND,
  brandCssText,
  brandCssVariables,
  buildMetadata,
  defaultChatSampleQuestions,
  normalizeBrand,
  safeAssetUrl,
} from "@/lib/brand";
import { BRANDING_REVALIDATE_SECONDS, fetchBrand } from "@/lib/brand-server";
import { BrandProvider, useBrand, useBrandUpdater } from "@/lib/brand-context";

const sendQuery = vi.fn();
vi.mock("@/lib/chat-context", () => ({
  useChat: () => ({
    sessions: [],
    activeSessionId: null,
    messages: [],
    input: "",
    setInput: vi.fn(),
    sending: false,
    sendQuery,
    deleteExchange: vi.fn(),
    selectSession: vi.fn(),
    startNewSession: vi.fn(),
    renameSession: vi.fn(),
    removeSession: vi.fn(),
  }),
}));

// Imported after the mock is registered.
import ChatPage from "@/app/(app)/chat/page";

const CURRENT_PAYLOAD = {
  brand_name: "Acme Assist",
  brand_assistant_name: "AcmeBot",
  brand_support_email: "help@acme.example",
};

const FUTURE_PAYLOAD = {
  product_name: "Contoso IQ",
  assistant_name: "Conti",
  company_name: "Contoso",
  support_email: "support@contoso.example",
  logo_light_url: "/branding/assets/aaa",
  logo_dark_url: "https://cdn.contoso.example/logo-dark.svg",
  mark_light_url: "javascript:alert(1)",
  favicon_url: "//evil.example/favicon.ico",
  palette: { "50": "#F0F4FF", "600": "#3355CC", "700": "not-a-colour" },
  login_headline: "Welcome to Contoso",
  chat_sample_questions: ["How do I reset MFA?", "", 42, "What is Zero Trust?"],
};

function jsonResponse(body: unknown, ok = true): Response {
  return { ok, status: ok ? 200 : 500, json: async () => body } as Response;
}

afterEach(() => {
  document.documentElement.removeAttribute("style");
  vi.unstubAllEnvs();
});

describe("normalizeBrand", () => {
  it("accepts the current 3-field /branding payload and fills the rest with seeded defaults", () => {
    const brand = normalizeBrand(CURRENT_PAYLOAD);
    expect(brand.productName).toBe("Acme Assist");
    expect(brand.assistantName).toBe("AcmeBot");
    expect(brand.supportEmail).toBe("help@acme.example");
    expect(brand.logos).toEqual(DEFAULT_BRAND.logos);
    expect(brand.palette).toEqual({});
    expect(brand.chatSampleQuestions).toEqual(defaultChatSampleQuestions("Acme Assist"));
  });

  it("accepts the future payload (logos, palette, login copy, questions) and rejects unsafe values", () => {
    const brand = normalizeBrand(FUTURE_PAYLOAD);
    expect(brand.productName).toBe("Contoso IQ");
    expect(brand.companyName).toBe("Contoso");
    expect(brand.logos.light).toBe("/branding/assets/aaa");
    expect(brand.logos.dark).toBe("https://cdn.contoso.example/logo-dark.svg");
    expect(brand.logos.markLight).toBe(DEFAULT_BRAND.logos.markLight); // javascript: rejected
    expect(brand.favicon).toBe(DEFAULT_BRAND.favicon); // protocol-relative rejected
    expect(brand.palette).toEqual({ "50": "#F0F4FF", "600": "#3355CC" });
    expect(brand.login.headline).toBe("Welcome to Contoso");
    expect(brand.login.eyebrow).toBeNull();
    expect(brand.chatSampleQuestions).toEqual(["How do I reset MFA?", "What is Zero Trust?"]);
  });

  it("falls back to the seeded defaults (DeployIQ / AskDIQ / teal) for empty or junk payloads", () => {
    for (const junk of [null, undefined, "x", 42, {}]) {
      const brand = normalizeBrand(junk);
      expect(brand.productName).toBe("DeployIQ");
      expect(brand.assistantName).toBe("AskDIQ");
      expect(brandCssText(brand)).toBe("");
    }
  });

  it("uses primary_hex when no palette is sent", () => {
    expect(normalizeBrand({ primary_hex: "#123456" }).palette).toEqual({ "600": "#123456" });
  });

  it("safeAssetUrl only allows same-origin paths and https", () => {
    expect(safeAssetUrl("/logo.svg")).toBe("/logo.svg");
    expect(safeAssetUrl("https://cdn.example/logo.svg")).toBe("https://cdn.example/logo.svg");
    expect(safeAssetUrl("http://cdn.example/logo.svg")).toBeNull();
    expect(safeAssetUrl("data:image/svg+xml,<svg/>")).toBeNull();
    expect(safeAssetUrl("//evil.example/x.svg")).toBeNull();
  });
});

describe("fetchBrand (server layout fetch)", () => {
  it("returns the normalized brand and asks Next to revalidate every 300 s", async () => {
    vi.stubEnv("API_BASE_URL", "http://backend:8000/");
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(CURRENT_PAYLOAD));
    const brand = await fetchBrand(fetchMock as unknown as typeof fetch);
    expect(brand.productName).toBe("Acme Assist");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://backend:8000/branding");
    expect(init.next).toEqual({ revalidate: BRANDING_REVALIDATE_SECONDS });
    expect(BRANDING_REVALIDATE_SECONDS).toBe(300);
  });

  it("uses the seeded defaults when the fetch fails", async () => {
    const failing = vi.fn().mockRejectedValue(new Error("ECONNREFUSED"));
    expect(await fetchBrand(failing as unknown as typeof fetch)).toEqual(DEFAULT_BRAND);
  });

  it("uses the seeded defaults on a non-2xx response", async () => {
    const serverError = vi.fn().mockResolvedValue(jsonResponse({ detail: "boom" }, false));
    expect(await fetchBrand(serverError as unknown as typeof fetch)).toEqual(DEFAULT_BRAND);
  });
});

describe("generateMetadata (buildMetadata)", () => {
  it("takes the title and favicon from the brand, not a hard-coded DeployIQ", () => {
    const meta = buildMetadata(normalizeBrand({ ...FUTURE_PAYLOAD, favicon_url: "/branding/assets/fav" }));
    expect(meta.title).toBe("Contoso IQ | Governed Security & Compliance Assistant");
    expect(meta.icons).toMatchObject({ shortcut: "/branding/assets/fav" });
  });

  it("defaults to DeployIQ and /favicon.svg", () => {
    const meta = buildMetadata(DEFAULT_BRAND);
    expect(meta.title).toContain("DeployIQ");
    expect(meta.icons).toMatchObject({ shortcut: "/favicon.svg" });
  });
});

describe("BrandProvider", () => {
  function Name() {
    return <span data-testid="name">{useBrand().productName}</span>;
  }

  it("exposes the brand through useBrand()", () => {
    render(
      <BrandProvider brand={normalizeBrand(CURRENT_PAYLOAD)}>
        <Name />
      </BrandProvider>,
    );
    expect(screen.getByTestId("name")).toHaveTextContent("Acme Assist");
  });

  it("useBrand() outside the provider returns the seeded defaults", () => {
    render(<Name />);
    expect(screen.getByTestId("name")).toHaveTextContent("DeployIQ");
  });

  it("writes the tenant palette to the --brand-* CSS variables and cleans up on unmount", () => {
    const brand = normalizeBrand(FUTURE_PAYLOAD);
    const { unmount } = render(
      <BrandProvider brand={brand}>
        <Name />
      </BrandProvider>,
    );
    const style = document.documentElement.style;
    expect(style.getPropertyValue("--brand-600")).toBe("51 85 204");
    expect(style.getPropertyValue("--brand-50")).toBe("240 244 255");
    expect(brandCssVariables(brand)).toEqual({ "--brand-50": "240 244 255", "--brand-600": "51 85 204" });
    expect(brandCssText(brand)).toBe(":root{--brand-50:240 244 255;--brand-600:51 85 204;}");
    unmount();
    expect(style.getPropertyValue("--brand-600")).toBe("");
  });

  it("does not touch the CSS variables for the seeded brand", () => {
    render(
      <BrandProvider>
        <Name />
      </BrandProvider>,
    );
    expect(document.documentElement.style.getPropertyValue("--brand-600")).toBe("");
  });
});

describe("chat sample questions come from the brand", () => {
  beforeAll(() => {
    // jsdom has no scrolling; the chat page auto-scrolls on mount.
    Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo;
  });

  it("renders the brand's questions and assistant name", () => {
    render(
      <BrandProvider brand={normalizeBrand(FUTURE_PAYLOAD)}>
        <ChatPage />
      </BrandProvider>,
    );
    expect(screen.getByText("How do I reset MFA?")).toBeInTheDocument();
    expect(screen.getByText("What is Zero Trust?")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Conti");
    expect(screen.queryByText(/DeployIQ/)).not.toBeInTheDocument();
  });

  it("falls back to the default questions with the product name", () => {
    render(<ChatPage />);
    expect(
      screen.getByText("What is the Essential Eight and how does DeployIQ implement it?"),
    ).toBeInTheDocument();
  });
});

describe("BrandProvider runtime updates (Settings save, PR-26)", () => {
  function Refresher() {
    const { refresh, setBrand } = useBrandUpdater();
    const brand = useBrand();
    return (
      <div>
        <span data-testid="product">{brand.productName}</span>
        <button type="button" onClick={() => void refresh()}>
          refresh
        </button>
        <button type="button" onClick={() => setBrand(DEFAULT_BRAND)}>
          defaults
        </button>
      </div>
    );
  }

  afterEach(() => vi.unstubAllGlobals());

  it("refresh() re-reads /branding through the proxy and applies names and every --brand-* variable", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(FUTURE_PAYLOAD));
    vi.stubGlobal("fetch", fetchMock);
    render(
      <BrandProvider brand={normalizeBrand(CURRENT_PAYLOAD)}>
        <Refresher />
      </BrandProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "refresh" }));
    await waitFor(() => expect(screen.getByTestId("product")).toHaveTextContent("Contoso IQ"));
    expect(fetchMock.mock.calls[0][0]).toBe("/api/proxy/branding");
    const style = document.documentElement.style;
    expect(style.getPropertyValue("--brand-600")).toBe("51 85 204");
    // Unchanged shades are written too, so they beat the server-rendered <style>.
    expect(style.getPropertyValue("--brand-800")).toBe("0 88 79");

    // Going back to the defaults still overrides a stale server palette.
    fireEvent.click(screen.getByRole("button", { name: "defaults" }));
    await waitFor(() => expect(style.getPropertyValue("--brand-600")).toBe("0 128 128"));
  });
});
