import React, { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MotionProvider } from "@/components/providers/MotionProvider";
import { DrawerShell } from "@/components/ui/DrawerShell";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";

function Harness({
  onPrev,
  onNext,
  withConfirm = false,
}: {
  onPrev?: () => void;
  onNext?: () => void;
  withConfirm?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  return (
    <MotionProvider>
      <button type="button" onClick={() => setOpen(true)}>
        Open inspector
      </button>
      <DrawerShell
        open={open}
        onClose={() => setOpen(false)}
        title="Trace inspector"
        description="Request ID: req-1"
        onPrev={onPrev}
        onNext={onNext}
        fullViewHref="/admin/governance?tab=Traces&request_id=req-1"
        footer={<span>Footer</span>}
      >
        <button type="button">First action</button>
        <a href="#x">Last link</a>
        {withConfirm ? (
          <button type="button" onClick={() => setConfirming(true)}>
            Delete
          </button>
        ) : null}
      </DrawerShell>
      <ConfirmDialog
        open={confirming}
        title="Delete record?"
        onConfirm={() => setConfirming(false)}
        onCancel={() => setConfirming(false)}
      />
    </MotionProvider>
  );
}

async function openDrawer() {
  const trigger = screen.getByRole("button", { name: "Open inspector" });
  trigger.focus();
  fireEvent.click(trigger);
  const dialog = await screen.findByRole("dialog", { name: /trace inspector/i });
  return { trigger, dialog };
}

describe("DrawerShell (F0.6.1)", () => {
  it("is a labelled modal dialog with description, footer and an Open full view link", async () => {
    render(<Harness />);
    const { dialog } = await openDrawer();
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAccessibleDescription("Request ID: req-1");
    expect(screen.getByRole("link", { name: /open full view/i })).toHaveAttribute(
      "href",
      "/admin/governance?tab=Traces&request_id=req-1",
    );
    expect(screen.getByText("Footer")).toBeInTheDocument();
  });

  it("moves focus to the close button, traps Tab inside and returns focus to the trigger on Esc", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const { trigger } = await openDrawer();
    const close = screen.getByRole("button", { name: "Close" });
    await waitFor(() => expect(close).toHaveFocus());

    // Tab past the last focusable element wraps to the first one in the drawer.
    screen.getByRole("link", { name: "Last link" }).focus();
    await user.tab();
    expect(screen.getByRole("link", { name: /open full view/i })).toHaveFocus();
    // Shift+Tab from the first element wraps to the last one.
    await user.tab({ shift: true });
    expect(screen.getByRole("link", { name: "Last link" })).toHaveFocus();

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(trigger).toHaveFocus();
  });

  it("closes on backdrop click", async () => {
    render(<Harness />);
    await openDrawer();
    fireEvent.click(screen.getByTestId("drawer-backdrop"));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("offers previous / next navigation and disables a missing direction", async () => {
    const onNext = vi.fn();
    render(<Harness onNext={onNext} />);
    await openDrawer();
    expect(screen.getByRole("button", { name: "Previous record" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Next record" }));
    expect(onNext).toHaveBeenCalledTimes(1);
  });

  it("hides prev / next when no navigation is given", async () => {
    render(<Harness />);
    await openDrawer();
    expect(screen.queryByRole("button", { name: "Next record" })).not.toBeInTheDocument();
  });

  it("lets a stacked ConfirmDialog own Esc and focus, then hands both back to the drawer", async () => {
    const user = userEvent.setup();
    render(<Harness withConfirm />);
    await openDrawer();
    const del = screen.getByRole("button", { name: "Delete" });
    del.focus();
    fireEvent.click(del);
    await screen.findByRole("dialog", { name: "Delete record?" });
    await waitFor(() => expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus());

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Delete record?" })).not.toBeInTheDocument());
    // The drawer is still open and focus is back on the button that opened the dialog.
    expect(screen.getByRole("dialog", { name: /trace inspector/i })).toBeInTheDocument();
    expect(del).toHaveFocus();
  });
});
