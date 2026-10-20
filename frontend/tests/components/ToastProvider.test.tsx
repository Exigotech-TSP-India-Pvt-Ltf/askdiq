import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MotionProvider } from "@/components/providers/MotionProvider";
import { ApiError } from "@/lib/api";
import { CONFLICT_TOAST, HTTP_CONFLICT, useApiMutation } from "@/lib/query-cache";
import { TOAST_DURATION_MS, ToastProvider, useToast, type ToastOptions } from "@/components/ui/ToastProvider";

afterEach(() => vi.useRealTimers());

function Trigger(props: ToastOptions) {
  const { show } = useToast();
  return (
    <button type="button" onClick={() => show(props)}>
      Notify
    </button>
  );
}

function renderWithToasts(options: ToastOptions) {
  return render(
    <MotionProvider>
      <ToastProvider>
        <Trigger {...options} />
      </ToastProvider>
    </MotionProvider>,
  );
}

describe("ToastProvider", () => {
  it("announces toasts through a persistent role=status live region", () => {
    renderWithToasts({ title: "Feedback promoted", description: "Added to the golden set", tone: "success" });
    const region = screen.getByRole("status", { name: "Notifications" });
    // The live region exists before any toast, so screen readers pick up insertions.
    expect(region).toHaveAttribute("aria-live", "polite");
    expect(region).toBeEmptyDOMElement();

    fireEvent.click(screen.getByRole("button", { name: "Notify" }));
    expect(region).toHaveTextContent("Feedback promoted");
    expect(region).toHaveTextContent("Added to the golden set");
  });

  it("runs the undo action once and dismisses the toast", async () => {
    const onUndo = vi.fn();
    renderWithToasts({ title: "Row dismissed", onUndo });
    fireEvent.click(screen.getByRole("button", { name: "Notify" }));
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(onUndo).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.queryByText("Row dismissed")).not.toBeInTheDocument());
  });

  it("auto-dismisses, and pauses the timer while hovered", async () => {
    vi.useFakeTimers();
    renderWithToasts({ title: "Saved" });
    fireEvent.click(screen.getByRole("button", { name: "Notify" }));
    const toast = screen.getByText("Saved").closest("[data-tone]") as HTMLElement;

    act(() => vi.advanceTimersByTime(TOAST_DURATION_MS - 1000));
    fireEvent.mouseEnter(toast);
    expect(toast).toHaveAttribute("data-paused", "true");
    // Well past the original deadline: still visible while hovered.
    act(() => vi.advanceTimersByTime(TOAST_DURATION_MS * 2));
    expect(screen.getByText("Saved")).toBeInTheDocument();

    fireEvent.mouseLeave(toast);
    act(() => vi.advanceTimersByTime(500));
    expect(screen.getByText("Saved")).toBeInTheDocument();
    expect(toast).toHaveAttribute("data-paused", "false");
    act(() => vi.advanceTimersByTime(600));
    // Removed from state; the exit animation then unmounts it.
    vi.useRealTimers();
    await waitFor(() => expect(screen.queryByText("Saved")).not.toBeInTheDocument());
  });

  it("closes from the dismiss button and keeps errors in the same region", async () => {
    renderWithToasts({ title: "Export failed", tone: "error", durationMs: 0 });
    fireEvent.click(screen.getByRole("button", { name: "Notify" }));
    expect(screen.getByRole("status", { name: "Notifications" })).toHaveTextContent("Export failed");
    fireEvent.click(screen.getByRole("button", { name: "Dismiss notification" }));
    await waitFor(() => expect(screen.queryByText("Export failed")).not.toBeInTheDocument());
  });

  it("useToast is a safe no-op outside the provider", () => {
    render(<Trigger title="Nothing" />);
    fireEvent.click(screen.getByRole("button", { name: "Notify" }));
    expect(screen.queryByText("Nothing")).not.toBeInTheDocument();
  });
});

describe("useApiMutation conflict toast (PR-16 data layer + F0.6 toasts)", () => {
  function SaveButton({ onConflict }: { onConflict?: () => void }) {
    const { mutate } = useApiMutation<string, never>({
      mutationFn: () => Promise.reject(new ApiError("version mismatch", HTTP_CONFLICT)),
      onConflict,
    });
    return (
      <button type="button" onClick={() => void mutate("x")}>
        Save
      </button>
    );
  }

  it("shows the 'changed by someone else' toast on a 409 by default", async () => {
    render(
      <MotionProvider>
        <ToastProvider>
          <SaveButton />
        </ToastProvider>
      </MotionProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(screen.getByRole("status", { name: "Notifications" })).toHaveTextContent(CONFLICT_TOAST.title),
    );
  });

  it("a custom onConflict replaces the default toast", async () => {
    const onConflict = vi.fn();
    render(
      <MotionProvider>
        <ToastProvider>
          <SaveButton onConflict={onConflict} />
        </ToastProvider>
      </MotionProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(onConflict).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("status", { name: "Notifications" })).toBeEmptyDOMElement();
  });
});
