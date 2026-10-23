import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { TaxonomyTab } from "@/components/admin/settings/TaxonomyTab";
import {
  isDuplicateTaxonomyCode,
  taxonomyFilterOptions,
  taxonomyLabel,
  type TaxonomyRead,
} from "@/lib/settings-api";
import { Providers, entry, installFetch, json, problem, resetSettingsState, taxonomyFixture, type Call } from "./harness";

beforeEach(resetSettingsState);
afterEach(() => {
  vi.unstubAllGlobals();
  resetSettingsState();
});

const LIST_URL = "/api/proxy/admin/insights/taxonomy?include_archived=true";

/** In-memory taxonomy backend implementing the PR-13 contract. */
function fakeBackend(items: TaxonomyRead[] = taxonomyFixture()) {
  const store = items.map((i) => ({ ...i }));
  return installFetch(({ method, url, body }: Call) => {
    if (method === "GET" && url === LIST_URL) return json({ items: store });
    if (method === "POST" && url === "/api/proxy/admin/insights/taxonomy") {
      const b = body as { kind: TaxonomyRead["kind"]; code: string; label: string };
      if (store.some((i) => i.kind === b.kind && i.code === b.code)) {
        return problem(409, "A taxonomy entry with this kind and code already exists.");
      }
      const created = entry({ ...b, is_system: false });
      store.push(created);
      return json(created, 201);
    }
    const match = url.match(/^\/api\/proxy\/admin\/insights\/taxonomy\/(\w+)\/(\w+)(\/archive|\/unarchive)?$/);
    if (match) {
      const row = store.find((i) => i.kind === match[1] && i.code === match[2]);
      if (!row) return problem(404, "Taxonomy entry not found.");
      if (method === "POST" && match[3] === "/unarchive") {
        row.is_active = true;
        return json({ ...row });
      }
      if (method === "POST" && match[3]) {
        if (["OOS", "OK", "OTHER"].includes(row.code)) {
          return problem(409, "This code is used by the rule stage and cannot be archived.");
        }
        row.is_active = false;
        return json({ ...row });
      }
      if (method === "PATCH") {
        Object.assign(row, body);
        return json({ ...row });
      }
    }
    return undefined;
  });
}

function renderTab() {
  return render(
    <Providers>
      <TaxonomyTab />
    </Providers>,
  );
}

const row = (code: string) => screen.getByTestId(`taxonomy-row-${code}`);

describe("Settings → Insights Taxonomy", { timeout: 15_000 }, () => {
  it("lists intents in order with read-only codes and editable label, colour and group", async () => {
    fakeBackend();
    renderTab();
    await screen.findByTestId("taxonomy-row-SVC");
    const codes = screen.getAllByTestId(/^code-/).map((el) => el.textContent);
    expect(codes).toEqual(["SVC", "SEC", "OOS"]);
    // The code is plain text (never an input); the label is editable.
    expect(within(row("SVC")).queryByDisplayValue("SVC")).not.toBeInTheDocument();
    expect(within(row("SVC")).getByTitle("Codes cannot be changed after creation")).toHaveTextContent("SVC");
    expect(screen.getByLabelText("Label for SVC")).toHaveValue("Platform and services");
    expect(screen.getByLabelText("Group for SVC")).toHaveValue("Customer / prospect");
    expect(screen.getByRole("tab", { name: /Intents/ })).toHaveTextContent("3");
  });

  it("edits a label and saves only the changed field", async () => {
    const { calls } = fakeBackend();
    renderTab();
    await screen.findByTestId("taxonomy-row-SVC");
    fireEvent.change(screen.getByLabelText("Label for SVC"), { target: { value: "Platform & services" } });
    fireEvent.click(within(row("SVC")).getByRole("button", { name: /save/i }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    const patch = calls.find((c) => c.method === "PATCH");
    expect(patch?.url).toBe("/api/proxy/admin/insights/taxonomy/intent/SVC");
    expect(patch?.body).toEqual({ label: "Platform & services" });
    expect(await screen.findByText("SVC saved")).toBeInTheDocument();
  });

  it("reorders rows by renumbering sort_order", async () => {
    const { calls } = fakeBackend();
    renderTab();
    await screen.findByTestId("taxonomy-row-SVC");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Move SEC up" }));
    });
    await waitFor(() => expect(screen.getAllByTestId(/^code-/).map((el) => el.textContent)).toEqual(["SEC", "SVC", "OOS"]));
    const patches = calls.filter((c) => c.method === "PATCH").map((c) => [c.url.split("/").pop(), c.body]);
    expect(patches).toEqual([
      ["SEC", { sort_order: 10 }],
      ["SVC", { sort_order: 20 }],
    ]);
    expect(screen.getByRole("button", { name: "Move SEC up" })).toBeDisabled();
  });

  it("rejects a duplicate code client-side (archived codes included) and on a server 409", async () => {
    const { calls } = fakeBackend();
    renderTab();
    await screen.findByTestId("taxonomy-row-SVC");
    const form = screen.getByRole("form", { name: "Add intent" });
    const code = within(form).getByLabelText("Code");
    const label = within(form).getByLabelText("Label");

    fireEvent.change(code, { target: { value: "svc" } });
    expect(code).toHaveValue("SVC");
    fireEvent.change(label, { target: { value: "Services again" } });
    fireEvent.click(within(form).getByRole("button", { name: "Add" }));
    expect(await screen.findByTestId("taxonomy-add-error")).toHaveTextContent("SVC already exists for this kind");

    // Archived LEGACY is not visible but its code is still taken.
    fireEvent.change(code, { target: { value: "LEGACY" } });
    fireEvent.click(within(form).getByRole("button", { name: "Add" }));
    expect(await screen.findByTestId("taxonomy-add-error")).toHaveTextContent("LEGACY already exists");
    expect(calls.some((c) => c.method === "POST")).toBe(false);

    // A code created elsewhere meanwhile: the server answers 409. The error
    // testid is already present from the LEGACY check above, so findByTestId
    // alone resolves on that existing element before its text updates;
    // waitFor polls until the content actually reflects this submission.
    const { calls: later } = fakeBackend([...taxonomyFixture(), entry({ kind: "intent", code: "NEW", label: "x" })]);
    fireEvent.change(code, { target: { value: "NEW" } });
    fireEvent.click(within(form).getByRole("button", { name: "Add" }));
    await waitFor(() =>
      expect(screen.getByTestId("taxonomy-add-error")).toHaveTextContent("NEW already exists for this kind"),
    );
    expect(later.filter((c) => c.method === "POST")).toHaveLength(1);
  });

  it("rejects an invalid code pattern", async () => {
    fakeBackend();
    renderTab();
    await screen.findByTestId("taxonomy-row-SVC");
    const form = screen.getByRole("form", { name: "Add intent" });
    fireEvent.change(within(form).getByLabelText("Code"), { target: { value: "1ABC" } });
    fireEvent.change(within(form).getByLabelText("Label"), { target: { value: "Bad" } });
    fireEvent.click(within(form).getByRole("button", { name: "Add" }));
    expect(await screen.findByTestId("taxonomy-add-error")).toHaveTextContent("Code must start with a letter");
  });

  it("adds a new intent at the end of the list", async () => {
    const { calls } = fakeBackend();
    renderTab();
    await screen.findByTestId("taxonomy-row-SVC");
    const form = screen.getByRole("form", { name: "Add intent" });
    fireEvent.change(within(form).getByLabelText("Code"), { target: { value: "BILLING" } });
    fireEvent.change(within(form).getByLabelText("Label"), { target: { value: "Billing" } });
    fireEvent.change(within(form).getByLabelText("Group (optional)"), { target: { value: "Customer / prospect" } });
    fireEvent.click(within(form).getByRole("button", { name: "Add" }));
    expect(await screen.findByTestId("taxonomy-row-BILLING")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      kind: "intent",
      code: "BILLING",
      label: "Billing",
      color: null,
      group: "Customer / prospect",
      sort_order: 50,
    });
  });

  it("archives (never deletes) an entry: it disappears, and shows again under Show archived", async () => {
    const { calls } = fakeBackend();
    renderTab();
    await screen.findByTestId("taxonomy-row-SEC");
    expect(screen.queryByTestId("taxonomy-row-LEGACY")).not.toBeInTheDocument();

    fireEvent.click(within(row("SEC")).getByRole("button", { name: "Archive SEC" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Archive SEC?" });
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: "Archive" }));
    });
    await waitFor(() => expect(screen.queryByTestId("taxonomy-row-SEC")).not.toBeInTheDocument());
    expect(calls.find((c) => c.method === "POST")?.url).toBe("/api/proxy/admin/insights/taxonomy/intent/SEC/archive");
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);

    fireEvent.click(screen.getByRole("checkbox", { name: /show archived/i }));
    expect(within(row("SEC")).getByText("Archived")).toBeInTheDocument();
    expect(within(row("LEGACY")).getByText("Archived")).toBeInTheDocument();
    expect(screen.getByLabelText("Label for LEGACY")).toBeDisabled();
  });

  it("unarchives an archived entry", async () => {
    const { calls } = fakeBackend();
    renderTab();
    await screen.findByTestId("taxonomy-row-SVC");
    fireEvent.click(screen.getByRole("checkbox", { name: /show archived/i }));
    await act(async () => {
      fireEvent.click(within(row("LEGACY")).getByRole("button", { name: "Unarchive LEGACY" }));
    });
    expect(calls.find((c) => c.method === "POST")?.url).toBe("/api/proxy/admin/insights/taxonomy/intent/LEGACY/unarchive");
    await waitFor(() => expect(within(row("LEGACY")).getByText("Active")).toBeInTheDocument());
    expect(screen.getByLabelText("Label for LEGACY")).toBeEnabled();
  });

  it("edits description and the substantive flag, sending only those fields", async () => {
    const { calls } = fakeBackend();
    renderTab();
    await screen.findByTestId("taxonomy-row-SVC");
    fireEvent.change(screen.getByLabelText("Description for SVC"), { target: { value: "Questions about the platform" } });
    fireEvent.click(screen.getByLabelText("SVC is substantive"));
    fireEvent.click(within(row("SVC")).getByRole("button", { name: /save/i }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({
      description: "Questions about the platform",
      substantive: false,
    });
  });

  it("bot-quality values have an editable handled flag", async () => {
    fakeBackend();
    renderTab();
    await screen.findByTestId("taxonomy-row-SVC");
    fireEvent.click(screen.getByRole("tab", { name: /Bot quality/ }));
    expect(await screen.findByLabelText("OK counts as handled")).toBeInTheDocument();
    expect(screen.queryByLabelText("OK is substantive")).not.toBeInTheDocument();
  });

  it("core codes cannot be archived", async () => {
    fakeBackend();
    renderTab();
    await screen.findByTestId("taxonomy-row-OOS");
    expect(within(row("OOS")).getByRole("button", { name: "OOS is a core code and cannot be archived" })).toBeDisabled();
  });

  it("switches kinds: bot quality has no group column, issues list their own codes", async () => {
    fakeBackend();
    renderTab();
    await screen.findByTestId("taxonomy-row-SVC");
    fireEvent.click(screen.getByRole("tab", { name: /Bot quality/ }));
    expect(await screen.findByTestId("taxonomy-row-OK")).toBeInTheDocument();
    expect(screen.queryByLabelText("Group for OK")).not.toBeInTheDocument();
    expect(screen.getByRole("form", { name: "Add bot-quality value" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /Issues/ }));
    expect(await screen.findByTestId("taxonomy-row-KB_GAP")).toBeInTheDocument();
  });
});

describe("taxonomy helpers (filters and historic labels)", () => {
  const items = taxonomyFixture();

  it("archived entries disappear from filter options", () => {
    expect(taxonomyFilterOptions(items, "intent").map((o) => o.value)).toEqual(["SVC", "SEC", "OOS"]);
  });

  it("historic labels still render for archived codes, and unknown codes fall back to the code", () => {
    expect(taxonomyLabel(items, "intent", "LEGACY")).toBe("Legacy product");
    expect(taxonomyLabel(items, "intent", "GONE")).toBe("GONE");
  });

  it("duplicate detection is per kind and includes archived entries", () => {
    expect(isDuplicateTaxonomyCode(items, "intent", "LEGACY")).toBe(true);
    expect(isDuplicateTaxonomyCode(items, "issue", "SVC")).toBe(false);
  });
});
