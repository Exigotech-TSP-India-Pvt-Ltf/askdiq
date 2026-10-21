import React from "react";
import { describe, expect, it } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, within } from "@testing-library/react";
import { parseAnswer, renderAnswerMarkdown, type InlineRenderer } from "@/lib/answer-markdown";

function show(content: string, renderInline?: InlineRenderer) {
  return render(<>{renderAnswerMarkdown(content, { renderInline })}</>);
}

// A stand-in for the citation-pill renderer: "[n]" becomes a pill node.
const pills: InlineRenderer = (text, key) =>
  text.split(/(\[\d{1,3}\])/).map((part, i) =>
    /^\[\d{1,3}\]$/.test(part) ? (
      <span key={`${key}-${i}`} data-testid="pill">
        {part.slice(1, -1)}
      </span>
    ) : (
      <span key={`${key}-${i}`}>{part}</span>
    ),
  );

describe("parseAnswer", () => {
  it("renders plain text as one paragraph", () => {
    expect(parseAnswer("We pilot first.")).toEqual([{ type: "paragraph", text: "We pilot first." }]);
  });

  it("parses headings, bullets, steps and a nested list", () => {
    const blocks = parseAnswer(
      ["Lead.", "", "## Key points", "- **A** one", "  - nested", "- B", "", "1. First", "2. Second"].join("\n"),
    );
    expect(blocks.map((b) => b.type)).toEqual(["paragraph", "heading", "list", "list"]);
    const bullets = blocks[2];
    expect(bullets).toMatchObject({ ordered: false, items: [{ text: "**A** one", children: { items: ["nested"] } }, { text: "B" }] });
    expect(blocks[3]).toMatchObject({ ordered: true, start: 1, items: [{ text: "First" }, { text: "Second" }] });
  });

  it("parses a table only with a separator row", () => {
    const table = parseAnswer("| A | B |\n| --- | :--: |\n| 1 | 2 |\n| 3 |");
    expect(table).toEqual([{ type: "table", header: ["A", "B"], rows: [["1", "2"], ["3", ""]] }]);
    const noSeparator = parseAnswer("| A | B |\n| 1 | 2 |");
    expect(noSeparator.every((b) => b.type === "paragraph")).toBe(true);
  });

  it("keeps a malformed table as paragraphs", () => {
    const blocks = parseAnswer("| A | B |\n| --- |\n| 1 | 2 |");
    expect(blocks.every((b) => b.type === "paragraph")).toBe(true);
  });

  it("is linear on a 100 KB answer", () => {
    const big = "| a | b |\n| - | - |\n" + "| x | y |\n".repeat(5000) + "- item\n".repeat(5000) + "#".repeat(50_000);
    const started = performance.now();
    parseAnswer(big);
    expect(performance.now() - started).toBeLessThan(500);
  });
});

describe("renderAnswerMarkdown", () => {
  it("uses semantic headings below the page h1, lists and bold", () => {
    show("Lead.\n\n# Top\n## Key points\n### Detail\n- **MFA** for admins\n\n1. Connect\n2. Assess");
    expect(screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent)).toEqual(["Top", "Key points"]);
    expect(screen.getByRole("heading", { level: 3 })).toHaveTextContent("Detail");
    expect(screen.queryByRole("heading", { level: 1 })).toBeNull();
    expect(screen.getByText("MFA").tagName).toBe("STRONG");
    const lists = screen.getAllByRole("list");
    expect(lists.map((l) => l.tagName)).toEqual(["UL", "OL"]);
    expect(within(lists[1]).getAllByRole("listitem")).toHaveLength(2);
  });

  it("renders a table with column headers inside a scroll region (phone width)", () => {
    show("Main difference.\n\n| Aspect | E8 | ISO |\n| --- | --- | --- |\n| Scope | Eight | Annex A |");
    const region = screen.getByRole("region", { name: "Table" });
    expect(region).toHaveClass("overflow-x-auto", "max-w-full");
    expect(region).toHaveAttribute("tabindex", "0");
    const headers = within(region).getAllByRole("columnheader");
    expect(headers.map((h) => h.textContent)).toEqual(["Aspect", "E8", "ISO"]);
    headers.forEach((h) => expect(h).toHaveAttribute("scope", "col"));
    expect(within(region).getByRole("table")).toHaveClass("min-w-max");
    expect(region.parentElement).toHaveClass("min-w-0");
  });

  it("renders <script> and HTML as inert text", () => {
    const { container } = show('<script>alert(1)</script>\n- <img src=x onerror="alert(2)">\n| <b>x</b> | y |\n| - | - |\n| <iframe> | z |');
    expect(container.querySelector("script, img, iframe, b")).toBeNull();
    expect(screen.getByText("<script>alert(1)</script>")).toBeInTheDocument();
    expect(container.textContent).toContain('<img src=x onerror="alert(2)">');
  });

  it("never turns model links into anchors (javascript: is inert)", () => {
    const { container } = show("Click [here](javascript:alert(1)) or https://evil.example\n- [x](javascript:void(0))");
    expect(container.querySelector("a")).toBeNull();
    expect(container.textContent).toContain("[here](javascript:alert(1))");
  });

  it("leaves an unmatched ** literal", () => {
    show("Price is **not bold");
    expect(screen.getByText(/Price is/)).toHaveTextContent("Price is **not bold");
  });

  it("passes citation pill nodes through in paragraphs, bullets and table cells", () => {
    show("We pilot first [1].\n- Drift checks [2]\n\n| A | B |\n| --- | --- |\n| Scope [3] | x |", pills);
    const found = screen.getAllByTestId("pill");
    expect(found.map((p) => p.textContent)).toEqual(["1", "2", "3"]);
    expect(found[1].closest("li")).not.toBeNull();
    expect(found[2].closest("td")).not.toBeNull();
  });
});
