import React from "react";
import { describe, expect, it } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, within } from "@testing-library/react";
import { MarkdownView, parseMarkdown, safeHref } from "@/components/documents/MarkdownView";

const SOURCE = [
  "# Leave policy",
  "",
  "Staff get **20 days** of *annual* leave. See [the portal](https://example.com/hr).",
  "",
  "## Rules",
  "",
  "- Book two weeks ahead",
  "- Use `HR-42` form",
  "",
  "1. Ask",
  "2. Wait",
  "",
  "| Type | Days |",
  "| --- | --- |",
  "| Annual | 20 |",
  "| Sick | 10 |",
  "",
  "```",
  "<b>not bold</b>",
  "```",
  "",
  "> Quoted note",
  "",
  "---",
  "",
  '<script>alert("x")</script> and [bad](javascript:alert(1))',
].join("\n");

describe("MarkdownView", () => {
  it("parses the block types the converter emits", () => {
    expect(parseMarkdown(SOURCE).map((b) => b.type)).toEqual([
      "heading",
      "paragraph",
      "heading",
      "list",
      "list",
      "table",
      "code",
      "quote",
      "rule",
      "paragraph",
    ]);
  });

  it("renders headings, emphasis, lists, tables and safe links", () => {
    const { container } = render(<MarkdownView source={SOURCE} />);
    expect(screen.getByRole("heading", { name: "Leave policy" })).toBeInTheDocument();
    expect(screen.getByText("20 days").tagName).toBe("STRONG");
    expect(screen.getByText("annual").tagName).toBe("EM");
    expect(screen.getByRole("link", { name: "the portal" })).toHaveAttribute("href", "https://example.com/hr");
    expect(screen.getByRole("link", { name: "the portal" })).toHaveAttribute("rel", expect.stringContaining("noopener"));
    expect(screen.getAllByRole("list")).toHaveLength(2);
    const table = screen.getByRole("table");
    expect(within(table).getByRole("columnheader", { name: "Days" })).toBeInTheDocument();
    expect(within(table).getByRole("cell", { name: "Sick" })).toBeInTheDocument();
    expect(screen.getByText("HR-42").tagName).toBe("CODE");
    expect(container.querySelector("blockquote")).toHaveTextContent("Quoted note");
  });

  it("never turns source HTML or unsafe links into live markup", () => {
    const { container } = render(<MarkdownView source={SOURCE} />);
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
    expect(screen.getByText("<b>not bold</b>")).toBeInTheDocument();
    expect(screen.getByText(/<script>alert/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "bad" })).not.toBeInTheDocument();
    expect(screen.getByText("bad")).toBeInTheDocument();
    expect(safeHref("javascript:alert(1)")).toBeNull();
    expect(safeHref("mailto:hr@example.com")).toBe("mailto:hr@example.com");
  });
});
