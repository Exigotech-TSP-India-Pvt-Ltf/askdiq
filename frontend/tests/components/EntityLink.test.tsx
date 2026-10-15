import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MotionProvider } from "@/components/providers/MotionProvider";
import { EntityLink } from "@/components/ui/EntityLink";
import { ENTITY_KINDS, entityHref, entityId, type EntityRef } from "@/lib/entity-links";

afterEach(() => vi.unstubAllGlobals());

const CASES: [EntityRef, string][] = [
  [{ kind: "visitor", id: "10.0.0.7" }, "/admin/usage?tab=sessions&ip=10.0.0.7"],
  [{ kind: "session", id: "s-1" }, "/admin/usage?tab=sessions&session=s-1"],
  [{ kind: "conversation", id: "c-1" }, "/admin/conversations/c-1"],
  [{ kind: "turn", id: "m-9", conversationId: "c-1" }, "/admin/conversations/c-1?turn=m-9"],
  [{ kind: "trace", id: "req-1" }, "/admin/governance?tab=Traces&request_id=req-1"],
  [{ kind: "feedback", id: "m-9" }, "/admin/feedback?message_id=m-9"],
  [{ kind: "document", id: "d-1" }, "/documents/d-1"],
  [{ kind: "chunk", id: "k-3", documentId: "d-1" }, "/documents/d-1?chunk=k-3"],
  [{ kind: "analytics", intent: "pricing", quality: "low" }, "/admin/analytics?intent=pricing&quality=low"],
];

describe("entityHref (newplan §1 deep links)", () => {
  it.each(CASES)("%o -> %s", (ref, href) => {
    expect(entityHref(ref)).toBe(href);
  });

  it("covers every kind", () => {
    expect(new Set(CASES.map(([ref]) => ref.kind))).toEqual(new Set(ENTITY_KINDS));
  });

  it("encodes ids so they cannot break out of the path or query", () => {
    expect(entityHref({ kind: "conversation", id: "../x?y=1" })).toBe("/admin/conversations/..%2Fx%3Fy%3D1");
    expect(entityHref({ kind: "trace", id: "a&tab=Logs" })).toBe(
      "/admin/governance?tab=Traces&request_id=a%26tab%3DLogs",
    );
    expect(entityHref({ kind: "analytics" })).toBe("/admin/analytics");
    expect(entityId({ kind: "analytics", intent: "x" })).toBeNull();
  });
});

describe("EntityLink", () => {
  it("renders the id as a link to its page", () => {
    render(<EntityLink entity={{ kind: "trace", id: "req-42" }} />);
    const link = screen.getByRole("link", { name: "req-42" });
    expect(link).toHaveAttribute("href", "/admin/governance?tab=Traces&request_id=req-42");
    expect(link).toHaveAttribute("title", "Trace req-42");
  });

  it("copies the id and shows a Copied tooltip, without triggering the row click", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    const onRow = vi.fn();
    render(
      <MotionProvider>
        <div onClick={onRow}>
          <EntityLink entity={{ kind: "conversation", id: "c-77" }}>Open chat</EntityLink>
        </div>
      </MotionProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Copy conversation id" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Copied"));
    expect(writeText).toHaveBeenCalledWith("c-77");
    expect(onRow).not.toHaveBeenCalled();
  });

  it("stays usable when the clipboard is blocked", async () => {
    vi.stubGlobal("navigator", {
      ...navigator,
      clipboard: { writeText: vi.fn().mockRejectedValue(new Error("denied")) },
    });
    render(<EntityLink entity={{ kind: "document", id: "d-1" }} />);
    fireEvent.click(screen.getByRole("button", { name: "Copy document id" }));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "d-1" })).toBeInTheDocument();
  });

  it("has no copy button for filter-only links", () => {
    render(<EntityLink entity={{ kind: "analytics", intent: "billing" }}>Billing questions</EntityLink>);
    expect(screen.getByRole("link", { name: "Billing questions" })).toHaveAttribute(
      "href",
      "/admin/analytics?intent=billing",
    );
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
