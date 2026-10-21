import React from "react";
import { describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { ClarificationCard, summarizeAnswers, toAnswers } from "@/components/chat/ClarificationCard";
import { MessageBubble } from "@/components/MessageBubble";
import type { ChatMessage, Clarification } from "@/lib/types";

const two: Clarification = {
  preamble: "DeployIQ offers several services; which would you like to know about?",
  original_query: "do you buy?",
  questions: [
    {
      id: "Q1",
      header: "Topic",
      question: "What would you like to know about buying from DeployIQ?",
      options: [
        { id: "O1", label: "Buy a service", description: "Zero Trust Professional plan", sources: ["services.md"] },
        { id: "O2", label: "Pricing and plans", description: "Published plans" },
        { id: "O3", label: "Become a partner", description: "Reseller programme" },
      ],
    },
    {
      id: "Q2",
      header: "Region",
      question: "Which regions apply?",
      multi_select: true,
      options: [
        { id: "O1", label: "Australia", description: "AU customers" },
        { id: "O2", label: "New Zealand", description: "NZ customers" },
      ],
    },
  ],
};

const one: Clarification = { ...two, preamble: "", questions: [two.questions[0]] };

// The series the backend asks for an underspecified audit request.
const series: Clarification = {
  preamble: "Happy to help you get ready. A few quick details will let me give you a specific plan and timeline.",
  original_query: "We have an audit coming up. What should we do and how long will it take?",
  questions: [
    {
      id: "Q1",
      header: "Framework",
      question: "Which framework is the audit against?",
      options: [
        { id: "O1", label: "Essential Eight", description: "ACSC Essential Eight", sources: ["e8-service-page.md"] },
        { id: "O2", label: "CIS Benchmark", description: "CIS Microsoft 365" },
        { id: "O3", label: "ISO 27001", description: "ISO/IEC 27001 controls" },
      ],
    },
    {
      id: "Q2",
      header: "Maturity",
      question: "Where are your controls today?",
      options: [
        { id: "O1", label: "Not assessed yet" },
        { id: "O2", label: "Partly implemented" },
        { id: "O3", label: "Mostly in place" },
      ],
    },
    {
      id: "Q3",
      header: "Environment",
      question: "Which environment is in scope?",
      multi_select: true,
      options: [
        { id: "O1", label: "Microsoft 365" },
        { id: "O2", label: "Entra ID and Intune" },
      ],
    },
  ],
};

function key(el: Element, k: string) {
  fireEvent.keyDown(el, { key: k });
}

describe("ClarificationCard", () => {
  it("renders the questions with roles, number hints, descriptions and source hints", () => {
    render(<ClarificationCard clarification={two} onSubmit={vi.fn()} />);
    const card = screen.getByRole("region", { name: "Clarifying questions" });
    expect(within(card).getByText(two.preamble!)).toBeInTheDocument();
    const tabs = within(card).getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["1Topic", "2Region", "Review"]);
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
    expect(within(card).getByText("1 of 2")).toBeInTheDocument();
    const group = within(card).getByRole("radiogroup", { name: two.questions[0].question });
    const radios = within(group).getAllByRole("radio");
    expect(radios).toHaveLength(4); // 3 options + Other
    expect(radios[0]).toHaveAttribute("aria-checked", "false");
    const descId = radios[0].getAttribute("aria-describedby");
    expect(descId).toBeTruthy();
    expect(document.getElementById(descId!)).toHaveTextContent("Zero Trust Professional plan · from: services.md");
    expect(within(radios[0]).getByText("1")).toBeInTheDocument();
    expect(screen.getByText("Question 1 of 2")).toHaveAttribute("aria-live", "polite");
  });

  it("is driven by the keyboard: arrows move, numbers pick, Enter goes on, Space toggles, then sends all answers", () => {
    const onSubmit = vi.fn();
    render(<ClarificationCard clarification={two} onSubmit={onSubmit} autoFocus />);
    const radios = screen.getAllByRole("radio");
    expect(radios[0]).toHaveFocus(); // focus moves into the card
    key(radios[0], "ArrowDown");
    expect(radios[1]).toHaveFocus();
    key(radios[1], "ArrowUp");
    expect(radios[0]).toHaveFocus();
    key(radios[0], "3"); // number picks
    expect(radios[2]).toHaveAttribute("aria-checked", "true");
    expect(radios[2]).toHaveFocus();
    key(radios[2], "Enter"); // next question
    expect(screen.getByText("Question 2 of 2")).toBeInTheDocument();
    expect(screen.getAllByRole("tab")[0]).toHaveAccessibleName(/answered/);
    const boxes = screen.getAllByRole("checkbox");
    expect(boxes[0]).toHaveFocus();
    key(boxes[0], " ");
    key(boxes[0], "ArrowDown");
    key(boxes[1], " ");
    expect(boxes[0]).toHaveAttribute("aria-checked", "true");
    expect(boxes[1]).toHaveAttribute("aria-checked", "true");
    key(boxes[1], "Enter"); // review step
    expect(screen.getByText("Review your answers")).toBeInTheDocument();
    expect(screen.getByText("Become a partner")).toBeInTheDocument();
    expect(screen.getByText("Australia, New Zealand")).toBeInTheDocument();
    const send = screen.getByRole("button", { name: "Send answers" });
    expect(send).toHaveFocus();
    fireEvent.click(send);
    expect(onSubmit).toHaveBeenCalledWith(
      [
        { question_id: "Q1", option_ids: ["O3"] },
        { question_id: "Q2", option_ids: ["O1", "O2"] },
      ],
      "Topic: Become a partner · Region: Australia, New Zealand",
    );
    // Collapsed, read-only, cannot be re-submitted.
    expect(screen.getByRole("note", { name: "Your answers" })).toHaveTextContent(
      "Topic: Become a partner · Region: Australia, New Zealand",
    );
    expect(screen.queryByRole("button", { name: "Send answers" })).not.toBeInTheDocument();
  });

  it("renders a 3-question series and sends every answer from the keyboard", () => {
    const onSubmit = vi.fn();
    render(<ClarificationCard clarification={series} onSubmit={onSubmit} autoFocus />);
    const card = screen.getByRole("region", { name: "Clarifying questions" });
    expect(within(card).getByText(series.preamble!)).toBeInTheDocument();
    expect(within(card).getAllByRole("tab").map((t) => t.textContent)).toEqual([
      "1Framework",
      "2Maturity",
      "3Environment",
      "Review",
    ]);
    expect(screen.getByText("Question 1 of 3")).toBeInTheDocument();
    const first = screen.getAllByRole("radio");
    expect(first[0]).toHaveFocus();
    key(first[0], "1");
    key(first[0], "Enter");
    expect(screen.getByText("Question 2 of 3")).toBeInTheDocument();
    const second = screen.getAllByRole("radio");
    expect(second[0]).toHaveFocus();
    key(second[0], "2");
    key(second[1], "Enter");
    expect(screen.getByText("Question 3 of 3")).toBeInTheDocument();
    const boxes = screen.getAllByRole("checkbox");
    expect(boxes[0]).toHaveFocus();
    key(boxes[0], " ");
    key(boxes[0], "ArrowDown");
    key(boxes[1], " ");
    key(boxes[1], "Enter");
    expect(screen.getByText("Review your answers")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Send answers" }));
    expect(onSubmit).toHaveBeenCalledWith(
      [
        { question_id: "Q1", option_ids: ["O1"] },
        { question_id: "Q2", option_ids: ["O2"] },
        { question_id: "Q3", option_ids: ["O1", "O2"] },
      ],
      "Framework: Essential Eight · Maturity: Partly implemented · Environment: Microsoft 365, Entra ID and Intune",
    );
  });

  it("switches questions with the arrow keys and the Back button", () => {
    render(<ClarificationCard clarification={two} onSubmit={vi.fn()} />);
    const first = screen.getAllByRole("radio")[0];
    key(first, "ArrowRight");
    expect(screen.getByText("Question 2 of 2")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(screen.getByText("Question 1 of 2")).toBeInTheDocument();
    key(screen.getAllByRole("radio")[0], "ArrowRight");
    key(screen.getAllByRole("checkbox")[0], "ArrowLeft");
    expect(screen.getByText("Question 1 of 2")).toBeInTheDocument();
  });

  it("turns Other into a text field and sends its text", () => {
    const onSubmit = vi.fn();
    render(<ClarificationCard clarification={one} onSubmit={onSubmit} />);
    const other = screen.getByRole("radio", { name: /Other/ });
    fireEvent.click(other);
    const input = screen.getByRole("textbox", { name: /Your own answer/ });
    fireEvent.change(input, { target: { value: "Security audits" } });
    key(input, "1"); // typing digits does not pick an option
    expect(screen.getAllByRole("radio")[0]).toHaveAttribute("aria-checked", "false");
    key(input, "Enter");
    expect(onSubmit).toHaveBeenCalledWith([{ question_id: "Q1", option_ids: [], other_text: "Security audits" }], "Topic: Security audits");
  });

  it("single question: Enter on a row picks it and sends at once", () => {
    const onSubmit = vi.fn();
    render(<ClarificationCard clarification={one} onSubmit={onSubmit} autoFocus />);
    const radios = screen.getAllByRole("radio");
    key(radios[0], "ArrowDown");
    key(radios[1], "Enter");
    expect(onSubmit).toHaveBeenCalledWith([{ question_id: "Q1", option_ids: ["O2"] }], "Topic: Pricing and plans");
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
  });

  it("Esc dismisses and can be undone", () => {
    const onDismiss = vi.fn();
    render(<ClarificationCard clarification={one} onDismiss={onDismiss} />);
    key(screen.getAllByRole("radio")[0], "Escape");
    expect(onDismiss).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show questions" }));
    expect(screen.getAllByRole("radio").length).toBeGreaterThan(0);
  });

  it("does not steal focus from someone typing", () => {
    render(
      <>
        <textarea aria-label="composer" autoFocus />
        <ClarificationCard clarification={one} onSubmit={vi.fn()} autoFocus />
      </>,
    );
    expect(screen.getByRole("textbox", { name: "composer" })).toHaveFocus();
  });

  it("Next is disabled until the question is answered; disabled cards ignore picks", () => {
    const onSubmit = vi.fn();
    const { rerender } = render(<ClarificationCard clarification={two} onSubmit={onSubmit} />);
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    rerender(<ClarificationCard clarification={two} onSubmit={onSubmit} disabled />);
    fireEvent.click(screen.getAllByRole("radio")[0]);
    expect(screen.getAllByRole("radio")[0]).toHaveAttribute("aria-checked", "false");
  });

  it("builds the payload and summary helpers", () => {
    const selections = {
      Q1: { optionIds: ["O1"], other: false, otherText: "" },
      Q2: { optionIds: [], other: true, otherText: "Fiji" },
    };
    expect(toAnswers(two, selections)).toEqual([
      { question_id: "Q1", option_ids: ["O1"] },
      { question_id: "Q2", option_ids: [], other_text: "Fiji" },
    ]);
    expect(summarizeAnswers(two, selections)).toBe("Topic: Buy a service · Region: Fiji");
  });
});

describe("ClarificationCard recommended option", () => {
  // O2 clearly leads: tagged, but kept in place (shortcut 2) and not picked.
  const tagged: Clarification = {
    ...two,
    questions: [
      {
        ...two.questions[0],
        options: two.questions[0].options.map((o) => ({ ...o, recommended: o.id === "O2" })),
      },
      {
        ...two.questions[1],
        options: two.questions[1].options.map((o) => ({ ...o, recommended: o.id === "O1" })),
      },
    ],
  };

  it("shows the badge only on the recommended option, in the accessible name", () => {
    render(<ClarificationCard clarification={tagged} onSubmit={vi.fn()} />);
    const radios = screen.getAllByRole("radio");
    expect(screen.getAllByText("Recommended")).toHaveLength(1);
    expect(within(radios[1]).getByText("Recommended")).toBeInTheDocument();
    expect(within(radios[0]).queryByText("Recommended")).not.toBeInTheDocument();
    expect(radios[1]).toHaveAccessibleName(/Pricing and plans.*Recommended/);
    expect(screen.getByRole("radio", { name: /Recommended/ })).toBe(radios[1]);
    // Server order kept: the number hint still matches the position.
    expect(within(radios[1]).getByText("2")).toBeInTheDocument();
  });

  it("is never pre-selected: nothing is checked and Next stays disabled", () => {
    render(<ClarificationCard clarification={tagged} onSubmit={vi.fn()} />);
    for (const radio of screen.getAllByRole("radio")) {
      expect(radio).toHaveAttribute("aria-checked", "false");
    }
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  });

  it("keeps the digit shortcuts and answers unchanged, multi-select included", () => {
    const onSubmit = vi.fn();
    render(<ClarificationCard clarification={tagged} onSubmit={onSubmit} autoFocus />);
    const radios = screen.getAllByRole("radio");
    key(radios[0], "1"); // picks O1, not the recommended O2
    expect(radios[0]).toHaveAttribute("aria-checked", "true");
    expect(radios[1]).toHaveAttribute("aria-checked", "false");
    key(radios[0], "Enter");
    const boxes = screen.getAllByRole("checkbox");
    expect(within(boxes[0]).getByText("Recommended")).toBeInTheDocument();
    expect(boxes[0]).toHaveAttribute("aria-checked", "false");
    key(boxes[0], "2");
    key(boxes[1], "Enter");
    fireEvent.click(screen.getByRole("button", { name: "Send answers" }));
    expect(onSubmit).toHaveBeenCalledWith(
      [
        { question_id: "Q1", option_ids: ["O1"] },
        { question_id: "Q2", option_ids: ["O2"] },
      ],
      "Topic: Buy a service · Region: New Zealand",
    );
  });

  it("shows no badge without a recommendation, and at most one per question", () => {
    const { unmount } = render(<ClarificationCard clarification={two} onSubmit={vi.fn()} />);
    expect(screen.queryByText("Recommended")).not.toBeInTheDocument();
    unmount();
    const both: Clarification = {
      ...one,
      questions: [{ ...one.questions[0], options: one.questions[0].options.map((o) => ({ ...o, recommended: true })) }],
    };
    render(<ClarificationCard clarification={both} onSubmit={vi.fn()} />);
    expect(screen.getAllByText("Recommended")).toHaveLength(1);
  });
});

describe("MessageBubble with a clarification", () => {
  const message: ChatMessage = {
    id: "a1",
    role: "assistant",
    content: "What would you like to know?\n\n1. Buy a service…",
    createdAt: Date.now(),
    clarification: one,
  };

  it("renders the card instead of the numbered text fallback", () => {
    const onSubmit = vi.fn();
    render(<MessageBubble message={message} onClarificationSubmit={onSubmit} />);
    expect(screen.getByRole("region", { name: "Clarifying questions" })).toBeInTheDocument();
    expect(screen.queryByText(/1\. Buy a service/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: /Buy a service/ }));
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(onSubmit).toHaveBeenCalledWith([{ question_id: "Q1", option_ids: ["O1"] }], "Topic: Buy a service");
  });

  it("shows the summary once answered", () => {
    render(<MessageBubble message={message} clarificationAnswered="Topic: Buy a service" />);
    expect(screen.getByRole("note", { name: "Your answers" })).toHaveTextContent("Topic: Buy a service");
  });
});
