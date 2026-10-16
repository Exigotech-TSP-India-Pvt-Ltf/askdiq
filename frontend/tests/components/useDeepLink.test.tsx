import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { mergeSearch, useDeepLink, useDeepLinkParam } from "@/lib/use-deep-link";

/**
 * A small in-memory router history: replace() swaps the current entry, push()
 * adds one, back() / forward() move between entries. useSearchParams reads the
 * current entry, like the App Router does.
 */
const history = vi.hoisted(() => {
  const state = { entries: ["/admin/conversations/c-1"], index: 0, listeners: new Set<() => void>() };
  const current = () => state.entries[state.index];
  const notify = () => state.listeners.forEach((listener) => listener());
  return {
    state,
    current,
    reset(url: string) {
      state.entries = [url];
      state.index = 0;
    },
    replace: vi.fn((url: string) => {
      state.entries[state.index] = url;
      notify();
    }),
    push: vi.fn((url: string) => {
      state.entries = [...state.entries.slice(0, state.index + 1), url];
      state.index += 1;
      notify();
    }),
    back() {
      state.index = Math.max(0, state.index - 1);
      notify();
    },
    forward() {
      state.index = Math.min(state.entries.length - 1, state.index + 1);
      notify();
    },
  };
});

vi.mock("next/navigation", async () => {
  const { useSyncExternalStore } = await import("react");
  const subscribe = (cb: () => void) => {
    history.state.listeners.add(cb);
    return () => {
      history.state.listeners.delete(cb);
    };
  };
  const useUrl = () => useSyncExternalStore(subscribe, history.current);
  return {
    useRouter: () => ({ replace: history.replace, push: history.push }),
    usePathname: () => useUrl().split("?")[0],
    useSearchParams: () => new URLSearchParams(useUrl().split("?")[1] ?? ""),
  };
});

beforeEach(() => {
  history.reset("/admin/conversations/c-1");
  history.replace.mockClear();
  history.push.mockClear();
});

describe("mergeSearch", () => {
  it("sets, overwrites and removes keys", () => {
    expect(mergeSearch("tab=Traces&x=1", { x: 2, request_id: "r" })).toBe("?tab=Traces&x=2&request_id=r");
    expect(mergeSearch("a=1", { a: null })).toBe("");
    expect(mergeSearch("", { a: "" })).toBe("");
  });
});

describe("useDeepLink (F0.6.1)", () => {
  it("composes several writes in one tick (a toolbar 'Reset all' clears every filter)", () => {
    history.reset("/admin/governance?tab=Logs&level=ERROR&service=system&q=boom");
    const { result } = renderHook(() => useDeepLink());
    act(() => {
      result.current.set({ q: null });
      result.current.set({ service: null });
      result.current.set({ level: null });
    });
    expect(history.current()).toBe("/admin/governance?tab=Logs");
    // A later write after the URL settled starts from the new URL.
    act(() => result.current.set({ level: "INFO" }));
    expect(history.current()).toBe("/admin/governance?tab=Logs&level=INFO");
  });

  it("after Back, a write starts from the restored URL (no undone state comes back)", () => {
    history.reset("/admin/governance?tab=Logs");
    const { result } = renderHook(() => useDeepLink());
    act(() => result.current.set({ level: "ERROR" }, { history: "push" }));
    act(() => history.back());
    expect(history.current()).toBe("/admin/governance?tab=Logs");
    act(() => result.current.set({ q: "boom" }));
    expect(history.current()).toBe("/admin/governance?tab=Logs&q=boom");
  });

  it("writes with router.replace and scroll:false, keeping other params", () => {
    history.reset("/admin/conversations/c-1?audience=users");
    const { result } = renderHook(() => useDeepLink());
    act(() => result.current.set({ turn: "m-3" }));
    expect(history.replace).toHaveBeenCalledWith("/admin/conversations/c-1?audience=users&turn=m-3", {
      scroll: false,
    });
    expect(history.push).not.toHaveBeenCalled();
    expect(result.current.get("turn")).toBe("m-3");
    expect(result.current.get("audience")).toBe("users");
  });

  it("round-trips through back and forward when history: push is used", () => {
    const { result } = renderHook(() => useDeepLinkParam("turn", { history: "push" }));
    expect(result.current[0]).toBeNull();

    act(() => result.current[1]("m-1"));
    act(() => result.current[1]("m-2"));
    expect(history.push).toHaveBeenLastCalledWith("/admin/conversations/c-1?turn=m-2", { scroll: false });
    expect(result.current[0]).toBe("m-2");

    act(() => history.back());
    expect(result.current[0]).toBe("m-1");
    act(() => history.back());
    expect(result.current[0]).toBeNull();
    act(() => history.forward());
    expect(result.current[0]).toBe("m-1");

    act(() => result.current[1](null));
    expect(history.push).toHaveBeenLastCalledWith("/admin/conversations/c-1", { scroll: false });
    expect(result.current[0]).toBeNull();
  });

  it("restores the view from a shared URL on first render", () => {
    history.reset("/admin/governance?tab=Traces&request_id=req-9");
    const { result } = renderHook(() => useDeepLinkParam("request_id"));
    expect(result.current[0]).toBe("req-9");
  });
});
