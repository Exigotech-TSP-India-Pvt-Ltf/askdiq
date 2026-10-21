/**
 * Settings > Knowledge links (OR-7): the source URL domain allow-list (chips,
 * validated hosts, single leading wildcard), the "Require a source URL on
 * upload" switch, Save with the row version (409 reloads), superadmin only.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import SettingsPage from "@/app/(app)/admin/settings/page";
import type { KnowledgeSettingsRead } from "@/lib/settings-api";
import { mockFetch, renderPage, type Route } from "../../helpers/entity-harness";

vi.mock("next/navigation", async () => (await import("../../helpers/router-mock")).navigationMock());

function settings(overrides: Partial<KnowledgeSettingsRead> = {}): KnowledgeSettingsRead {
  return {
    allowed_hosts: [],
    require_source_url: false,
    version: 1,
    updated_at: null,
    updated_by: null,
    max_hosts: 50,
    max_host_length: 253,
    ...overrides,
  };
}

let server: KnowledgeSettingsRead;
let puts: unknown[];

function routes(put?: (body: Record<string, unknown>) => { status?: number; body?: unknown }): Route[] {
  return [
    ["GET", /^\/knowledge\/settings$/, () => ({ body: server })],
    [
      "PUT",
      /^\/knowledge\/settings$/,
      (_url, init) => {
        const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
        puts.push(body);
        if (put) return put(body);
        server = settings({
          allowed_hosts: body.allowed_hosts as string[],
          require_source_url: body.require_source_url as boolean,
          version: server.version + 1,
          updated_at: "2026-10-21T08:00:00Z",
          updated_by: "sam@contoso.example",
        });
        return { body: server };
      },
    ],
  ];
}

beforeEach(() => {
  server = settings();
  puts = [];
});
afterEach(() => vi.unstubAllGlobals());

const open = (role: "superadmin" | "admin" = "superadmin") => renderPage(<SettingsPage />, { role, url: "/admin/settings?tab=knowledge" });

describe("Settings > Knowledge links", () => {
  it("is a tab for superadmins only (knowledge:links)", async () => {
    mockFetch(routes());
    open();
    expect(await screen.findByRole("tab", { name: /Knowledge links/ })).toBeInTheDocument();
    expect(await screen.findByText("The list is empty: any https domain is allowed.")).toBeInTheDocument();

    open("admin");
    expect(screen.queryByRole("tab", { name: /Knowledge links/ })).not.toBeInTheDocument();
  });

  it("adds validated hosts as chips, removes them, flips the switch and saves with the version", async () => {
    mockFetch(routes());
    open();
    const input = await screen.findByLabelText("Add a domain");

    fireEvent.change(input, { target: { value: "https://docs.example.com/page" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Enter only the domain");

    fireEvent.change(input, { target: { value: "a.*.example.com" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(screen.getByRole("alert")).toHaveTextContent("Only a single leading wildcard");

    fireEvent.change(input, { target: { value: "Docs.DeployIQ.com.au" } });
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.change(input, { target: { value: "*.example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    fireEvent.change(input, { target: { value: "old.example.org" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove old.example.org" }));

    const list = screen.getByRole("list", { name: "Allowed domains" });
    expect(within(list).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["docs.deployiq.com.au", "*.example.com"]);

    const toggle = screen.getByRole("switch", { name: "Require a source URL on upload" });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-checked", "true");

    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() =>
      expect(puts).toEqual([{ allowed_hosts: ["docs.deployiq.com.au", "*.example.com"], require_source_url: true, version: 1 }]),
    );
    expect(await screen.findByText(/by sam@contoso\.example/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
  });

  it("shows a server 422 (invalid host) and reloads on a 409", async () => {
    mockFetch(routes(() => ({ status: 422, body: { detail: "Invalid host: bad_host.example" } })));
    open();
    fireEvent.click(await screen.findByRole("switch", { name: "Require a source URL on upload" }));
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid host: bad_host.example");

    server = settings({ version: 5, allowed_hosts: ["docs.example.com"], require_source_url: true });
    mockFetch(routes(() => ({ status: 409, body: { detail: "Stale version" } })));
    open();
    fireEvent.click(await screen.findByRole("switch", { name: "Require a source URL on upload" }));
    // Fresh state arrives on the reload after the conflict.
    server = settings({ version: 6, allowed_hosts: ["docs.example.com", "help.example.com"], require_source_url: true });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText("Knowledge link settings were changed by someone else")).toBeInTheDocument();
    expect(await screen.findByText("help.example.com")).toBeInTheDocument();
  });
});
