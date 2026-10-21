import React from "react";
import { describe, expect, it } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { ClientIpCell } from "@/components/admin/governance/ClientIpCell";
import type { GovernanceLogItem } from "@/lib/governance-api";

function log(over: Partial<GovernanceLogItem>): GovernanceLogItem {
  return {
    id: "1",
    created_at: "2026-10-21T10:00:00Z",
    level: "INFO",
    service: "auth",
    action: "login_success",
    summary: "Signed in",
    detail: {},
    user_id: "a@example.com",
    ip_address: null,
    ip_source: null,
    request_id: "req-1",
    ...over,
  };
}

describe("ClientIpCell", () => {
  it("shows a recorded IP without a hint", () => {
    render(<ClientIpCell log={log({ ip_address: "203.0.113.7", ip_source: "recorded" })} />);
    expect(screen.getByText("203.0.113.7")).toBeInTheDocument();
    expect(screen.queryByText("linked")).not.toBeInTheDocument();
  });

  it.each([
    ["session", /sign-in session/],
    ["request", /same request/],
  ] as const)("marks a %s-correlated IP as linked and says where it came from", (source, why) => {
    render(<ClientIpCell log={log({ ip_address: "198.51.100.4", ip_source: source })} />);
    expect(screen.getByText("198.51.100.4")).toBeInTheDocument();
    expect(screen.getByText("linked")).toBeInTheDocument();
    expect(screen.getByText(why)).toHaveClass("sr-only");
  });

  it("says why a system job has no IP", () => {
    render(<ClientIpCell log={log({ request_id: "system", user_id: "system" })} />);
    const cell = screen.getByText("Not recorded");
    expect(cell).toHaveAttribute("title", "Not recorded: system job, no client.");
  });

  it("never shows a dash or an invented value when nothing was captured", () => {
    render(<ClientIpCell log={log({})} />);
    expect(screen.getByText("Not recorded")).toHaveAttribute("title", expect.stringMatching(/no client IP was captured/));
    expect(screen.queryByText("—")).not.toBeInTheDocument();
  });

  it("treats an IP from an older API (no ip_source) as recorded", () => {
    render(<ClientIpCell log={log({ ip_address: "10.0.0.1", ip_source: undefined })} />);
    expect(screen.getByText("10.0.0.1")).toBeInTheDocument();
    expect(screen.queryByText("linked")).not.toBeInTheDocument();
  });
});
