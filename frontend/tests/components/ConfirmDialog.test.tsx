import React, { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MotionProvider } from "@/components/providers/MotionProvider";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";

function Harness({ onConfirm, confirmText }: { onConfirm: () => void | Promise<void>; confirmText?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <MotionProvider>
      <button type="button" onClick={() => setOpen(true)}>
        Delete document
      </button>
      <ConfirmDialog
        open={open}
        tone="danger"
        title="Delete policy.pdf?"
        description="This removes the document and all of its chunks."
        confirmLabel="Delete"
        confirmText={confirmText}
        onConfirm={async () => {
          await onConfirm();
          setOpen(false);
        }}
        onCancel={() => setOpen(false)}
      />
    </MotionProvider>
  );
}

async function open() {
  const trigger = screen.getByRole("button", { name: "Delete document" });
  trigger.focus();
  fireEvent.click(trigger);
  return { trigger, dialog: await screen.findByRole("alertdialog", { name: "Delete policy.pdf?" }) };
}

describe("ConfirmDialog (F0.6.2)", () => {
  it("blocks a mismatched typed confirmation and allows the exact text", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(<Harness onConfirm={onConfirm} confirmText="policy.pdf" />);
    const { dialog } = await open();
    expect(dialog).toHaveAccessibleDescription("This removes the document and all of its chunks.");

    const input = screen.getByLabelText(/type policy\.pdf to confirm/i);
    await waitFor(() => expect(input).toHaveFocus());
    const confirm = screen.getByRole("button", { name: "Delete" });
    expect(confirm).toBeDisabled();

    await user.type(input, "policy.PDF");
    expect(confirm).toBeDisabled();
    expect(input).toHaveAttribute("aria-invalid", "true");
    // Enter on a mismatch does not submit either.
    await user.keyboard("{Enter}");
    expect(onConfirm).not.toHaveBeenCalled();

    await user.clear(input);
    await user.type(input, "policy.pdf");
    expect(confirm).toBeEnabled();
    await user.click(confirm);
    expect(onConfirm).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
  });

  it("without confirmText focuses Cancel; Esc cancels and focus returns to the trigger", async () => {
    const onConfirm = vi.fn();
    render(<Harness onConfirm={onConfirm} />);
    const { trigger } = await open();
    await waitFor(() => expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus());
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(onConfirm).not.toHaveBeenCalled();
    expect(trigger).toHaveFocus();
  });

  it("clears the typed text when reopened", async () => {
    const user = userEvent.setup();
    render(<Harness onConfirm={vi.fn()} confirmText="DELETE" />);
    await open();
    await user.type(screen.getByLabelText(/type delete to confirm/i), "DEL");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    await open();
    expect(screen.getByLabelText(/type delete to confirm/i)).toHaveValue("");
  });
});
