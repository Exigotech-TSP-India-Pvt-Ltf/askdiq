import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";

import { Sidebar } from "@/components/Sidebar";
import { queryCache } from "@/lib/query-cache";
import AdminEvaluationPage from "@/app/(app)/admin/evaluation/page";
import EvaluationPage from "@/app/(app)/evaluation/page";
import { DEFAULT_USAGE_TAB, USAGE_TAB_IDS, parseUsageTab } from "@/lib/usage-tabs";

// next/navigation's redirect() throws to abort rendering; the mock does the same.
const { redirectMock } = vi.hoisted(() => ({
  redirectMock: vi.fn((url: string) => {
    throw new Error(`NEXT_REDIRECT:${url}`);
  }),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/admin",
  redirect: redirectMock,
}));

vi.mock("@/lib/auth-context", async () => {
  const { authValue, makeMe } = await import("../helpers/auth");
  const value = authValue(makeMe("superadmin"));
  return { useAuth: () => value };
});

// Every item a superadmin sees (role filtering is covered in Sidebar.test).
const NAV_ITEMS: ReadonlyArray<readonly [label: string, href: string]> = [
  ["Dashboard", "/admin"],
  ["Chat", "/chat"],
  ["Documents", "/documents"],
  ["Visitors", "/admin/users"],
  ["Usage & Cost", "/admin/usage"],
  ["Conversation Logs", "/admin/conversations"],
  ["Feedback", "/admin/feedback"],
  ["Evaluation (Live)", "/admin/evaluation-live"],
  ["Governance", "/admin/governance"],
  ["Analytics", "/admin/analytics"],
  ["User Management", "/admin/user-management"],
  ["Settings", "/admin/settings"],
];

function navLinks(container: HTMLElement): HTMLAnchorElement[] {
  const nav = container.querySelector("nav");
  if (!nav) throw new Error("sidebar <nav> not rendered");
  return within(nav).getAllByRole("link") as HTMLAnchorElement[];
}

describe("Navigation journey (Phase F2)", () => {
  beforeEach(() => {
    window.localStorage.clear();
    redirectMock.mockClear();
    queryCache.clear();
    // Pending access-request count for the User Management badge: none.
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 })));
  });

  afterEach(() => vi.unstubAllGlobals());

  describe("sidebar items", () => {
    it("lists the current items in order with their routes", () => {
      const { container } = render(<Sidebar />);

      const links = navLinks(container);
      expect(links.map((a) => [a.textContent, a.getAttribute("href")])).toEqual(
        NAV_ITEMS.map(([label, href]) => [label, href]),
      );
    });

    it("does not link to the retired /admin/evaluation route", () => {
      const { container } = render(<Sidebar />);

      const hrefs = navLinks(container).map((a) => a.getAttribute("href"));
      expect(hrefs).not.toContain("/admin/evaluation");
      expect(hrefs).not.toContain("/evaluation");
    });
  });

  describe("collapsed rail", () => {
    it("shows every item as an icon with its label as the tooltip and accessible name", () => {
      const { container } = render(<Sidebar />);
      fireEvent.click(screen.getByTitle("Collapse sidebar"));

      const links = navLinks(container);
      expect(links).toHaveLength(NAV_ITEMS.length);
      for (const [label, href] of NAV_ITEMS) {
        const link = screen.getByRole("link", { name: label });
        expect(link).toHaveAttribute("href", href);
        expect(link).toHaveAttribute("title", label);
        expect(link.textContent).toBe("");
        expect(link.querySelector("svg")).not.toBeNull();
      }
    });

    it("renders the Sparkles icon for Evaluation (Live)", () => {
      render(<Sidebar />);
      fireEvent.click(screen.getByTitle("Collapse sidebar"));

      const link = screen.getByRole("link", { name: "Evaluation (Live)" });
      expect(link.querySelector("svg.lucide-sparkles")).not.toBeNull();
    });

    it("drops the tooltips again when expanded", () => {
      render(<Sidebar />);

      for (const [label] of NAV_ITEMS) {
        expect(screen.getByText(label).closest("a")).not.toHaveAttribute("title");
      }
    });
  });

  describe("legacy evaluation routes", () => {
    it.each([
      ["/admin/evaluation", AdminEvaluationPage],
      ["/evaluation", EvaluationPage],
    ])("%s redirects to /admin/evaluation-live", (_route, Page) => {
      expect(() => Page()).toThrow("NEXT_REDIRECT:/admin/evaluation-live");
      expect(redirectMock).toHaveBeenCalledOnce();
      expect(redirectMock).toHaveBeenCalledWith("/admin/evaluation-live");
    });
  });

  describe("Usage & Cost ?tab= alias parsing", () => {
    it.each([
      ["live_telemetry", "overview"],
      ["overall", "overview"],
      ["overview", "overview"],
      ["users", "users"],
      ["sessions", "sessions"],
      ["queries", "queries"],
    ])("?tab=%s -> %s", (param, expected) => {
      expect(parseUsageTab(param)).toBe(expected);
    });

    it.each([null, undefined, "", "bogus", "OVERVIEW", "__proto__", "constructor", "toString"])(
      "falls back to the default tab for %j",
      (param) => {
        expect(parseUsageTab(param)).toBe(DEFAULT_USAGE_TAB);
      },
    );

    it("keeps Overview first and no longer exposes live_telemetry as a tab id", () => {
      expect(DEFAULT_USAGE_TAB).toBe("overview");
      expect(USAGE_TAB_IDS[0]).toBe("overview");
      expect(USAGE_TAB_IDS).not.toContain("live_telemetry");
    });
  });
});
