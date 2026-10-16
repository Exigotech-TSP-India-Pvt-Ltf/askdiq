import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import GlobalError from "@/app/global-error";

afterEach(() => vi.restoreAllMocks());

describe("app/global-error (root layout failures)", () => {
  it("renders without any provider, router or brand context and offers a retry", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined); // <html> inside the test container
    const reset = vi.fn();
    render(<GlobalError error={Object.assign(new Error("boom"), { digest: "abc123" })} reset={reset} />);
    expect(screen.getByRole("heading", { name: "This page could not be loaded" })).toBeInTheDocument();
    expect(screen.getByText("abc123")).toBeInTheDocument();
    expect(screen.queryByText("boom")).not.toBeInTheDocument(); // no error details leak
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(reset).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("link", { name: "Go to the start page" })).toHaveAttribute("href", "/");
  });
});
