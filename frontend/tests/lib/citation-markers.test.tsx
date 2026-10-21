/**
 * OR-7: one shared helper for stored answers outside the chat (admin views):
 * markers become pills when citations are known, otherwise they are stripped.
 */
import React from "react";
import { describe, expect, it } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { stripCitationMarkers } from "@/lib/citation-markers";
import { createCitationInlineRenderer, renderCitedText } from "@/lib/format-text";
import type { Citation } from "@/lib/types";

const CITATION: Citation = {
  n: 2,
  document_id: "doc-2",
  title: "Pricing guide",
  url: "https://docs.deployiq.com.au/pricing",
  label: "deployiq",
  host: "docs.deployiq.com.au",
};

describe("stripCitationMarkers", () => {
  it("removes markers and the blanks before them", () => {
    expect(stripCitationMarkers("Claim [1]. Other [2][3] text [4] [5].")).toBe("Claim. Other text.");
    expect(stripCitationMarkers("No markers [here]")).toBe("No markers [here]");
    expect(stripCitationMarkers(null)).toBeNull();
    expect(stripCitationMarkers(undefined)).toBeUndefined();
  });

  it("is linear on 100 KB adversarial input", () => {
    const inputs = ["[".repeat(100_000), "[1".repeat(50_000), " ".repeat(100_000) + "[1]", "[12]".repeat(25_000)];
    const started = performance.now();
    for (const input of inputs) stripCitationMarkers(input);
    expect(performance.now() - started).toBeLessThan(1000);
  });
});

describe("renderCitedText / createCitationInlineRenderer", () => {
  it("without citations: plain text with markers stripped (no pills)", () => {
    render(<p data-testid="answer">{renderCitedText("Plans start at $10 [2].")}</p>);
    expect(screen.getByTestId("answer")).toHaveTextContent(/^Plans start at \$10\.$/);
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("with citations: source pills with safe new-tab links", () => {
    render(<p>{renderCitedText("Plans start at $10 [2][9].", [CITATION])}</p>);
    const pill = screen.getByRole("link", { name: /Source: deployiq, Pricing guide, opens in a new tab/ });
    expect(pill).toHaveAttribute("href", "https://docs.deployiq.com.au/pricing");
    expect(pill).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.queryByText("[9]")).toBeNull();
  });

  it("matches the renderInline(text, key) hook shape", () => {
    const renderInline = createCitationInlineRenderer([CITATION]);
    render(<p>{renderInline("Yes [2].", "k1")}</p>);
    expect(screen.getAllByRole("link")).toHaveLength(1);
    expect(createCitationInlineRenderer(null)("Yes [2].", "k2")).toBe("Yes.");
  });
});
