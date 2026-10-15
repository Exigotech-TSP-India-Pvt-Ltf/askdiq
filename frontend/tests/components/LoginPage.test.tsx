import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MotionProvider } from "@/components/providers/MotionProvider";
import { BrandProvider } from "@/lib/brand-context";
import { DEFAULT_BRAND, normalizeBrand, type Brand } from "@/lib/brand";
import { LoginView } from "@/components/auth/LoginView";
import { signInUrl } from "@/components/auth/MicrosoftSignInButton";
import { GENERIC_LOGIN_ERROR, LOGIN_ERROR_MESSAGES } from "@/lib/login-errors";
import LoginPage from "@/app/login/page";

const redirectTo = vi.hoisted(() => vi.fn());
vi.mock("@/lib/navigation", () => ({ redirectTo }));

function mockReducedMotion(reduce: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      matches: reduce && query.includes("prefers-reduced-motion"),
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  );
}

/** What `GET /branding` (PR-08 shape) returns for a white-labelled tenant. */
const BRANDING = {
  product_name: "Contoso IQ",
  assistant_name: "AskContoso",
  company_name: "Contoso",
  login_eyebrow: "CONTOSO AI",
  login_headline: "Answers you can trust.",
  login_subtext: "Grounded in Contoso's own knowledge base.",
  login_description: "Use your Contoso work account.",
  privacy_url: "https://contoso.example/privacy",
  terms_url: "",
  help_url: "/help",
};

function renderLogin(
  props: Partial<React.ComponentProps<typeof LoginView>> = {},
  brand: Brand = normalizeBrand(BRANDING),
) {
  return render(
    <BrandProvider brand={brand}>
      <MotionProvider>
        <LoginView errorCode={null} next={null} contactEmail="it@contoso.example" year={2026} {...props} />
      </MotionProvider>
    </BrandProvider>,
  );
}

beforeEach(() => {
  mockReducedMotion(false);
  redirectTo.mockClear();
});
afterEach(() => vi.unstubAllGlobals());

describe("Login page (F1.2.1, newplan §7.7)", () => {
  it("renders every string from the brand: panel copy, welcome, description and © footer", () => {
    renderLogin();
    const panel = screen.getByRole("complementary", { name: "Contoso IQ introduction" });
    expect(panel).toHaveTextContent("CONTOSO AI");
    expect(within(panel).getByRole("heading", { name: "Answers you can trust." })).toBeInTheDocument();
    expect(panel).toHaveTextContent("Grounded in Contoso's own knowledge base.");
    expect(panel).toHaveTextContent("© 2026 Contoso");
    expect(screen.getByRole("heading", { level: 1, name: "Welcome to Contoso IQ" })).toBeInTheDocument();
    expect(screen.getByText("Use your Contoso work account.")).toBeInTheDocument();
    expect(screen.queryByText(/Exigo Tech|DeployIQ/)).not.toBeInTheDocument();
  });

  it("falls back to the DeployIQ defaults when login_* fields are absent (current backend)", () => {
    renderLogin({}, DEFAULT_BRAND);
    expect(screen.getByRole("heading", { level: 1, name: "Welcome to DeployIQ" })).toBeInTheDocument();
    expect(screen.getByText("ASKDIQ")).toBeInTheDocument();
    expect(screen.getByText(/Sign in with your Exigo Tech Microsoft account/)).toBeInTheDocument();
  });

  it("uses the white logo on the teal panel (both themes)", () => {
    renderLogin({}, DEFAULT_BRAND);
    const panel = screen.getByRole("complementary");
    const logo = within(panel).getByRole("img", { name: "Exigo Tech" });
    expect(logo).toHaveAttribute("src", DEFAULT_BRAND.logos.dark);
    expect(logo.className).not.toContain("dark:hidden");
    expect(panel.className).toContain("from-brand-600");
    expect(panel.className).toContain("to-brand-800");
    // Single column below md: the panel hides and a teal header strip shows the logo.
    expect(panel.className).toContain("hidden");
    expect(panel.className).toContain("md:flex");
  });

  it("shows the SINGLE SIGN-ON divider and the access text with a mailto to ACCESS_CONTACT_EMAIL", () => {
    renderLogin();
    expect(screen.getByText(/single sign-on/i)).toHaveClass("uppercase");
    expect(screen.getByText(/Access is provisioned by your Contoso administrator/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "it@contoso.example" })).toHaveAttribute("href", "mailto:it@contoso.example");
  });

  it("says 'your administrator' when no contact email is configured", () => {
    renderLogin({ contactEmail: null });
    expect(screen.getByText(/contact your administrator\./)).toBeInTheDocument();
  });

  it("shows only the Privacy / Terms / Help links that are set", () => {
    renderLogin();
    const nav = screen.getByRole("navigation", { name: "Legal and help" });
    expect(within(nav).getByRole("link", { name: "Privacy Policy" })).toHaveAttribute("href", "https://contoso.example/privacy");
    expect(within(nav).getByRole("link", { name: "Privacy Policy" })).toHaveAttribute("rel", "noopener noreferrer");
    expect(within(nav).getByRole("link", { name: "Help" })).toHaveAttribute("href", "/help");
    expect(within(nav).queryByRole("link", { name: "Terms of Service" })).not.toBeInTheDocument();
  });

  it("hides the link row entirely when no link is configured", () => {
    renderLogin({}, DEFAULT_BRAND);
    expect(screen.queryByRole("navigation", { name: "Legal and help" })).not.toBeInTheDocument();
  });

  it("drops unsafe link URLs from /branding", () => {
    renderLogin({}, normalizeBrand({ ...BRANDING, privacy_url: "javascript:alert(1)", help_url: "//evil.example" }));
    expect(screen.queryByRole("navigation", { name: "Legal and help" })).not.toBeInTheDocument();
  });

  it("the Microsoft button shows a spinner and is disabled after a click (no double submit)", () => {
    renderLogin({ next: "/admin/usage" });
    const button = screen.getByRole("button", { name: "Sign in with Microsoft" });
    expect(button.querySelectorAll("rect")).toHaveLength(4);
    fireEvent.click(button);
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(button).toHaveTextContent("Redirecting to Microsoft");
    expect(button.querySelector(".animate-spin")).not.toBeNull();
    expect(redirectTo).toHaveBeenCalledWith("/auth/login?next=%2Fadmin%2Fusage");
    fireEvent.click(button);
    expect(redirectTo).toHaveBeenCalledTimes(1);
  });

  it("has a visible focus ring and hover lift classes", () => {
    renderLogin();
    const button = screen.getByRole("button", { name: "Sign in with Microsoft" });
    expect(button.className).toContain("focus-visible:ring-2");
    expect(button.className).toContain("hover:-translate-y-0.5");
    expect(button.className).toContain("rounded-xl");
  });

  it.each(Object.entries(LOGIN_ERROR_MESSAGES))("maps ?error=%s to its fixed message", (code, message) => {
    renderLogin({ errorCode: code });
    expect(screen.getByRole("alert")).toHaveTextContent(message);
  });

  it("shows a generic message for an unknown code and never reflects the raw input (XSS)", () => {
    const payload = '<img src=x onerror="alert(1)">';
    const { container } = renderLogin({ errorCode: payload });
    expect(screen.getByRole("alert")).toHaveTextContent(GENERIC_LOGIN_ERROR);
    expect(container.innerHTML).not.toContain("onerror");
    expect(container.querySelector("img[src='x']")).toBeNull();
  });

  it("shows no error banner without ?error", () => {
    renderLogin();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("animates the decoration normally, and applies no animation variants under reduced motion", () => {
    const { unmount } = renderLogin();
    expect(screen.getByTestId("brand-panel-decor")).toHaveAttribute("data-motion", "animated");
    unmount();

    mockReducedMotion(true);
    const { container } = renderLogin({ errorCode: "expired" });
    expect(screen.getByTestId("brand-panel-decor")).toHaveAttribute("data-motion", "static");
    expect(container.firstElementChild).toHaveAttribute("data-motion", "static");
    // Static: no element carries a transform or a hidden (opacity 0) entrance state.
    for (const el of Array.from(container.querySelectorAll<HTMLElement>("[style]"))) {
      expect(el.style.transform === "" || el.style.transform === "none").toBe(true);
      expect(el.style.opacity === "" || el.style.opacity === "1").toBe(true);
    }
  });

  it("is a split screen that uses theme tokens on the card side (dark mode follows the tokens)", () => {
    const { container } = renderLogin();
    const root = container.firstElementChild as HTMLElement;
    expect(root.className).toContain("md:flex-row");
    expect(root.className).toContain("bg-surface-page");
    expect(screen.getByRole("main").className).not.toMatch(/bg-white|text-gray/);
  });
});

describe("/login page wrapper", () => {
  it("passes only a validated same-origin next to the button", () => {
    render(
      <MotionProvider>
        {LoginPage({ searchParams: { next: "https://evil.example", error: "expired" } })}
      </MotionProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Sign in with Microsoft" }));
    expect(redirectTo).toHaveBeenCalledWith("/auth/login");
    expect(screen.getByRole("alert")).toHaveTextContent(LOGIN_ERROR_MESSAGES.expired);
  });

  it("signInUrl encodes next", () => {
    expect(signInUrl("/admin?tab=a&b=c")).toBe("/auth/login?next=%2Fadmin%3Ftab%3Da%26b%3Dc");
    expect(signInUrl(null)).toBe("/auth/login");
  });
});
