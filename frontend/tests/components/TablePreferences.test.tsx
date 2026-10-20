import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { TableToolbar } from "@/components/admin/TableToolbar";
import { TableCard } from "@/components/admin/TableCard";
import { DataTable, type ColumnDef } from "@/components/admin/DataTable";
import { tablePrefsKey, useTablePreferences } from "@/lib/use-table-preferences";

type Row = { id: string; email: string; role: string; last_login: string };

const COLUMNS: ColumnDef<Row>[] = [
  { key: "email", header: "Email" },
  { key: "role", header: "Role" },
  { key: "last_login", header: "Last login" },
];
const ROWS: Row[] = [{ id: "u1", email: "a@example.com", role: "admin", last_login: "today" }];

function UsersTable() {
  const prefs = useTablePreferences("users");
  return (
    <TableCard
      toolbar={
        <TableToolbar
          search=""
          onSearchChange={() => undefined}
          columnOptions={COLUMNS.map((c) => ({ key: c.key, label: c.header, hideable: c.key !== "email" }))}
          hiddenColumns={prefs.hiddenColumns}
          onToggleColumn={prefs.toggleColumn}
          density={prefs.density}
          onDensityChange={prefs.setDensity}
          exportAction={<button type="button">Export CSV</button>}
        />
      }
    >
      <DataTable columns={prefs.visibleColumns(COLUMNS)} data={ROWS} density={prefs.density} />
    </TableCard>
  );
}

function headers() {
  return screen.getAllByRole("columnheader").map((h) => h.textContent?.trim());
}

beforeEach(() => window.localStorage.clear());
afterEach(() => vi.restoreAllMocks());

describe("TableToolbar column visibility, density and export", () => {
  it("hides a column, switches density and persists both per table", () => {
    const { unmount } = render(<UsersTable />);
    expect(headers()).toEqual(["Email", "Role", "Last login"]);
    expect(screen.getByRole("button", { name: "Export CSV" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Columns" }));
    const menu = screen.getByRole("group", { name: "Visible columns" });
    // The not-hideable column is locked.
    expect(within(menu).getByLabelText("Email")).toBeDisabled();
    fireEvent.click(within(menu).getByLabelText("Role"));
    expect(headers()).toEqual(["Email", "Last login"]);

    const density = screen.getByRole("button", { name: "Compact" });
    expect(density).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(density);
    expect(density).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("a@example.com").closest("td")).toHaveAttribute("data-density", "compact");

    expect(JSON.parse(window.localStorage.getItem(tablePrefsKey("users")) ?? "{}")).toEqual({
      hidden: ["role"],
      density: "compact",
    });

    // A fresh mount restores the preferences.
    unmount();
    render(<UsersTable />);
    expect(headers()).toEqual(["Email", "Last login"]);
    expect(screen.getByRole("button", { name: "Compact" })).toHaveAttribute("aria-pressed", "true");
  });

  it("never hides the last visible column", () => {
    window.localStorage.setItem(tablePrefsKey("users"), JSON.stringify({ hidden: ["role"], density: "comfortable" }));
    render(<UsersTable />);
    fireEvent.click(screen.getByRole("button", { name: "Columns" }));
    fireEvent.click(screen.getByLabelText("Last login"));
    expect(headers()).toEqual(["Email"]);
  });

  it("ignores corrupt stored preferences", () => {
    window.localStorage.setItem(tablePrefsKey("users"), "{not json");
    render(<UsersTable />);
    expect(headers()).toEqual(["Email", "Role", "Last login"]);
  });

  it("keeps working in memory when storage is blocked", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    render(<UsersTable />);
    fireEvent.click(screen.getByRole("button", { name: "Columns" }));
    fireEvent.click(screen.getByLabelText("Last login"));
    expect(headers()).toEqual(["Email", "Role"]);
    fireEvent.click(screen.getByRole("button", { name: "Compact" }));
    expect(screen.getByRole("button", { name: "Compact" })).toHaveAttribute("aria-pressed", "true");
  });
});

describe("TableCard cursor-pagination variant", () => {
  it("renders Newer / Older controls driven by cursors", () => {
    const onNext = vi.fn();
    const onPrev = vi.fn();
    render(
      <TableCard cursor={{ hasPrev: false, hasNext: true, onPrev, onNext, pageItemCount: 50, itemLabel: "events" }}>
        <table />
      </TableCard>,
    );
    const nav = screen.getByRole("navigation", { name: "Cursor pagination" });
    expect(nav).toHaveTextContent("Showing 50 events");
    expect(within(nav).getByRole("button", { name: /newer/i })).toBeDisabled();
    fireEvent.click(within(nav).getByRole("button", { name: /older/i }));
    expect(onNext).toHaveBeenCalledTimes(1);
    expect(onPrev).not.toHaveBeenCalled();
  });

  it("prefers an explicit pagination footer over the cursor variant", () => {
    render(
      <TableCard
        pagination={<p>Page footer</p>}
        cursor={{ hasPrev: true, hasNext: true, onPrev: vi.fn(), onNext: vi.fn(), pageItemCount: 1 }}
      >
        <table />
      </TableCard>,
    );
    expect(screen.getByText("Page footer")).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Cursor pagination" })).not.toBeInTheDocument();
  });
});
