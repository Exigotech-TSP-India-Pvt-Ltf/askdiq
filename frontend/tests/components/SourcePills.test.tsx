/**
 * OR-7 inline source pills: `[n]` markers in an answer become grey pills
 * (links only for safe https URLs from the server's citation list), with a
 * hover / focus card, and chips under the answer that a link-less pill jumps to.
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { renderFormattedText } from "@/lib/format-text";
import { SourcePill } from "@/components/chat/SourcePill";
import { SourceChips, sourceChips } from "@/components/chat/SourceChips";
import { MessageBubble } from "@/components/MessageBubble";
import type { ChatMessage, Citation, SourceChunk } from "@/lib/types";

const DEPLOYIQ: Citation = {
  n: 1,
  document_id: "doc-1",
  title: "Essential Eight maturity",
  url: "https://docs.deployiq.com.au/e8",
  label: "deployiq",
  host: "docs.deployiq.com.au",
};
const GITHUB: Citation = {
  n: 3,
  document_id: "doc-3",
  title: "Release notes",
  url: "https://github.com/exigo/releases",
  label: "github",
  host: "github.com",
};
const NO_LINK: Citation = { n: 2, document_id: "doc-2", title: "Leave policy", url: null, label: "Leave-Policy", host: null };

afterEach(() => vi.useRealTimers());

function Answer({ text, citations }: { text: string; citations?: Citation[] }) {
  return <div>{renderFormattedText(text, { citations })}</div>;
}

describe("renderFormattedText source pills", () => {
  it("renders a linked pill with a new-tab name and rel=noopener noreferrer", () => {
    render(<Answer text="Patch apps within 48 hours.[1]" citations={[DEPLOYIQ]} />);
    const pill = screen.getByRole("link", { name: "Source: deployiq, Essential Eight maturity, opens in a new tab" });
    expect(pill).toHaveAttribute("href", "https://docs.deployiq.com.au/e8");
    expect(pill).toHaveAttribute("target", "_blank");
    expect(pill).toHaveAttribute("rel", "noopener noreferrer");
    expect(pill).toHaveTextContent("deployiq");
    expect(screen.queryByText(/\[1\]/)).not.toBeInTheDocument();
    expect(screen.getByText(/Patch apps within 48 hours\./)).toBeInTheDocument();
  });

  it("renders several pills for one claim ([1][3] and [1] [3])", () => {
    const { unmount } = render(<Answer text="Both apply.[1][3] Next sentence." citations={[DEPLOYIQ, GITHUB]} />);
    expect(screen.getAllByRole("link", { name: /^Source: / })).toHaveLength(2);
    expect(screen.getByText(/Next sentence\./)).toBeInTheDocument();
    unmount();
    render(<Answer text="Both apply. [1] [3]" citations={[DEPLOYIQ, GITHUB]} />);
    expect(screen.getAllByRole("link", { name: /^Source: / }).map((a) => a.textContent)).toEqual(["deployiq", "github"]);
  });

  it("refuses a javascript: (or http:) URL: the pill becomes a button, never a link", () => {
    render(
      <Answer
        text="Claim.[1] Other.[2]"
        citations={[
          { ...DEPLOYIQ, url: "javascript:alert(1)" },
          { ...NO_LINK, url: "http://insecure.example.com" },
        ]}
      />,
    );
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Source: deployiq, Essential Eight maturity" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Source: Leave-Policy, Leave policy" })).toBeInTheDocument();
  });

  it("drops unknown marker numbers and keeps pills inside bold and list items", () => {
    render(<Answer text={"- **Rotate keys**[1][9]\n- Plain"} citations={[DEPLOYIQ]} />);
    expect(screen.getAllByRole("link", { name: /^Source: / })).toHaveLength(1);
    expect(screen.queryByText(/\[9\]/)).not.toBeInTheDocument();
    expect(screen.getByText("Rotate keys").closest("strong")).not.toBeNull();
  });

  it("renders exactly as before without citations (markers are not parsed)", () => {
    const text = "Line one [1].\n- **Bold** item [2]";
    const { container: before } = render(<div>{renderFormattedText(text)}</div>);
    const { container: empty } = render(<div>{renderFormattedText(text, { citations: [] })}</div>);
    expect(empty.innerHTML).toBe(before.innerHTML);
    expect(before).toHaveTextContent("Line one [1].");
    expect(within(before).queryByRole("link")).not.toBeInTheDocument();
  });

  it("escapes everything (a crafted title or label is text, not markup)", () => {
    render(<Answer text="Claim.[1]" citations={[{ ...DEPLOYIQ, label: "<img src=x onerror=alert(1)>", title: "<b>x</b>" }]} />);
    expect(document.querySelector("img")).toBeNull();
    expect(screen.getByRole("link", { name: /^Source: <img/ })).toBeInTheDocument();
  });
});

describe("SourcePillCard", () => {
  it("opens on hover and on keyboard focus, closes on Esc and blur, and describes the pill", () => {
    render(<SourcePill citation={DEPLOYIQ} />);
    const pill = screen.getByRole("link", { name: /^Source: deployiq/ });
    const card = screen.getByTestId("source-pill-card");
    expect(pill).toHaveAttribute("aria-describedby", card.id);
    expect(card).toHaveAttribute("role", "tooltip");
    expect(card).not.toBeVisible();

    fireEvent.mouseEnter(pill.parentElement as HTMLElement);
    expect(card).toBeVisible();
    expect(card).toHaveTextContent("D");
    expect(card).toHaveTextContent("Essential Eight maturity");
    expect(card).toHaveTextContent("docs.deployiq.com.au");
    // Moving onto the card keeps it open (it sits inside the hover area).
    fireEvent.mouseEnter(card);
    expect(card).toBeVisible();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(card).not.toBeVisible();

    fireEvent.focus(pill);
    expect(card).toBeVisible();
    fireEvent.keyDown(pill, { key: "Escape" });
    expect(card).not.toBeVisible();
    fireEvent.focus(pill);
    fireEvent.blur(pill);
    expect(card).not.toBeVisible();
    expect(pill).toHaveAccessibleDescription(/Essential Eight maturity/);
  });

  it("never loads a third-party favicon: a monogram, or a generic icon for a label without letters", () => {
    const { container } = render(<SourcePill citation={{ ...DEPLOYIQ, label: "--" }} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("svg")).not.toBeNull();
  });
});

describe("SourceChips links and pill jump", () => {
  const sources: SourceChunk[] = [
    { chunk_id: "c1", document_id: "doc-1", content: "E8", score: 0.9, metadata: { source_name: "e8.md" }, url: "https://docs.deployiq.com.au/e8" },
    { chunk_id: "c2", document_id: "doc-2", content: "Leave", score: 0.8, metadata: { source_name: "leave.md" }, url: null },
  ];

  it("a chip is a link only for a safe https URL", () => {
    render(<SourceChips sources={[...sources, { ...sources[1], document_id: "doc-x", url: "javascript:alert(1)" }]} />);
    const link = screen.getByRole("link", { name: "e8.md, opens in a new tab" });
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link).toHaveAttribute("target", "_blank");
    expect(screen.getAllByRole("link")).toHaveLength(1);
  });

  it("history messages without chunks get one chip per citation; cited documents come first", () => {
    expect(sourceChips(undefined, [NO_LINK, DEPLOYIQ]).map((c) => c.name)).toEqual(["Leave policy", "Essential Eight maturity"]);
    expect(sourceChips(sources, [NO_LINK]).map((c) => c.documentId)).toEqual(["doc-2", "doc-1"]);
  });

  it("a pill without a link focuses and highlights the matching chip of its message", () => {
    vi.useFakeTimers();
    const message: ChatMessage = {
      id: "a1",
      role: "assistant",
      content: "Staff get 20 days.[2]",
      createdAt: Date.now(),
      citations: [NO_LINK],
    };
    render(<MessageBubble message={message} />);
    const pill = screen.getByRole("button", { name: "Source: Leave-Policy, Leave policy" });
    const chip = within(screen.getByRole("list", { name: "Sources" })).getByText("Leave policy").closest("li") as HTMLElement;
    fireEvent.click(pill);
    expect(chip).toHaveFocus();
    expect(chip).toHaveAttribute("data-highlighted", "true");
    act(() => {
      vi.runAllTimers();
    });
    expect(chip).not.toHaveAttribute("data-highlighted");
  });

  it("a message without citations renders its answer unchanged", () => {
    const message: ChatMessage = { id: "a2", role: "assistant", content: "Plain answer [1].", createdAt: Date.now() };
    render(<MessageBubble message={message} />);
    expect(screen.getByText("Plain answer [1].")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Sources" })).not.toBeInTheDocument();
  });
});
