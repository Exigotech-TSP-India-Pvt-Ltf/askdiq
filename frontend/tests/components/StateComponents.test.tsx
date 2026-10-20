import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MotionProvider } from "@/components/providers/MotionProvider";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { Skeleton, SkeletonCard, SkeletonRows, SkeletonText } from "@/components/ui/Skeleton";

afterEach(() => vi.unstubAllGlobals());

describe("EmptyState / ErrorState / Skeleton", () => {
  it("EmptyState shows the title, description and action", () => {
    render(<EmptyState title="No feedback yet" description="Ratings appear here." action={<button>Refresh</button>} />);
    expect(screen.getByText("No feedback yet")).toBeInTheDocument();
    expect(screen.getByText("Ratings appear here.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh" })).toBeInTheDocument();
  });

  it("ErrorState is an alert with retry and a copyable request id", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    const onRetry = vi.fn();
    render(
      <MotionProvider>
        <ErrorState message="The usage service timed out." requestId="req-500" onRetry={onRetry} />
      </MotionProvider>,
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Something went wrong");
    expect(alert).toHaveTextContent("The usage service timed out.");
    expect(alert).toHaveTextContent("req-500");

    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    expect(onRetry).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "Copy request id" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("req-500"));
  });

  it("ErrorState hides retry and request id when not given", () => {
    render(<ErrorState />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).not.toHaveTextContent("Request ID");
  });

  it("skeletons are decorative inside one busy status region", () => {
    const { container } = render(
      <>
        <SkeletonText lines={2} label="Loading summary" />
        <SkeletonRows rows={3} columns={2} label="Loading rows" />
        <SkeletonCard label="Loading chart" />
        <Skeleton />
      </>,
    );
    const summary = screen.getByRole("status", { name: "Loading summary" });
    expect(summary).toHaveAttribute("aria-busy", "true");
    expect(screen.getByRole("status", { name: "Loading rows" })).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "Loading chart" })).toBeInTheDocument();
    const blocks = container.querySelectorAll("[aria-hidden='true'].animate-pulse");
    expect(blocks.length).toBe(2 + 3 * 2 + 3 + 1);
    // Reduced motion: the pulse stops.
    blocks.forEach((block) => expect(block).toHaveClass("motion-reduce:animate-none"));
  });
});
