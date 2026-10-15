import { afterEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { ApiError } from "@/lib/api";
import {
  INTENT_DELAY_MS,
  QueryCache,
  prefetchNextPage,
  serializeKey,
  useApiMutation,
  useApiQuery,
  usePrefetchOnIntent,
  type Fetcher,
} from "@/lib/query-cache";

/** planchanges4 F0.5.1 to F0.5.3 (newplan §13.3). */

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("serializeKey", () => {
  it("joins tuple keys with / so prefixes match", () => {
    expect(serializeKey("admin/dashboard")).toBe("admin/dashboard");
    expect(serializeKey(["admin", "users", 2, null])).toBe("admin/users/2/");
  });
});

describe("F0.5.1 cache and dedupe", () => {
  it("5 simultaneous hooks with the same key make 1 fetch", async () => {
    const cache = new QueryCache();
    const fetcher = vi.fn(async () => ({ total: 7 }));
    const { result } = renderHook(() =>
      Array.from({ length: 5 }, () => useApiQuery(["admin", "dashboard"], fetcher, { cache })),
    );
    await waitFor(() => expect(result.current.every((q) => q.data?.total === 7)).toBe(true));
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("a key change aborts the previous request", async () => {
    const cache = new QueryCache();
    const signals: AbortSignal[] = [];
    const fetcher = vi.fn((signal: AbortSignal) => {
      signals.push(signal);
      return new Promise<string>(() => undefined);
    });
    const { rerender } = renderHook(({ k }) => useApiQuery(k, fetcher, { cache }), {
      initialProps: { k: "logs?page=1" },
    });
    await waitFor(() => expect(signals).toHaveLength(1));
    rerender({ k: "logs?page=2" });
    await waitFor(() => expect(signals[0].aborted).toBe(true));
    expect(signals).toHaveLength(2);
    expect(signals[1].aborted).toBe(false);
  });

  it("aborts on unmount but not while another hook still observes the key", async () => {
    const cache = new QueryCache();
    let signal: AbortSignal | undefined;
    const fetcher = (s: AbortSignal) => {
      signal = s;
      return new Promise<string>(() => undefined);
    };
    const a = renderHook(() => useApiQuery("k", fetcher, { cache }));
    const b = renderHook(() => useApiQuery("k", fetcher, { cache }));
    await waitFor(() => expect(signal).toBeDefined());
    a.unmount();
    await Promise.resolve();
    expect(signal?.aborted).toBe(false);
    b.unmount();
    await waitFor(() => expect(signal?.aborted).toBe(true));
  });

  it("a stale entry returns immediately, then refreshes in the background", async () => {
    const cache = new QueryCache();
    const t0 = 1_000_000;
    const now = vi.spyOn(Date, "now").mockReturnValue(t0);
    cache.setData("usage/summary", "old");
    now.mockReturnValue(t0 + 60_000);
    const pending = deferred<string>();
    const fetcher = vi.fn(() => pending.promise);
    const { result } = renderHook(() => useApiQuery("usage/summary", fetcher, { cache, staleTime: 30_000 }));
    expect(result.current.data).toBe("old");
    expect(result.current.isLoading).toBe(false);
    await waitFor(() => expect(result.current.isValidating).toBe(true));
    expect(fetcher).toHaveBeenCalledTimes(1);
    await act(async () => pending.resolve("new"));
    expect(result.current.data).toBe("new");
    expect(result.current.isValidating).toBe(false);
  });

  it("a fresh entry is served from cache without a request", () => {
    const cache = new QueryCache();
    cache.setData("k", 1);
    const fetcher = vi.fn(async () => 2);
    const { result } = renderHook(() => useApiQuery("k", fetcher, { cache, staleTime: 60_000 }));
    expect(result.current.data).toBe(1);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("revalidates stale observed keys on window focus", async () => {
    const cache = new QueryCache();
    let n = 0;
    const fetcher = vi.fn(async () => ++n);
    const { result } = renderHook(() => useApiQuery("k", fetcher, { cache, staleTime: 0 }));
    await waitFor(() => expect(result.current.data).toBe(1));
    act(() => {
      window.dispatchEvent(new Event("focus"));
    });
    await waitFor(() => expect(result.current.data).toBe(2));
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("invalidate(prefix) refetches observed keys under the prefix only", async () => {
    const cache = new QueryCache();
    const users = vi.fn(async () => "users");
    const docs = vi.fn(async () => "docs");
    renderHook(() => useApiQuery(["admin", "users"], users, { cache, staleTime: 60_000 }));
    renderHook(() => useApiQuery(["documents"], docs, { cache, staleTime: 60_000 }));
    await waitFor(() => expect(cache.getData(["admin", "users"])).toBe("users"));
    expect(cache.invalidate("admin/")).toBe(1);
    await waitFor(() => expect(users).toHaveBeenCalledTimes(2));
    expect(docs).toHaveBeenCalledTimes(1);
  });

  it("keeps the error and stays out of loading after a failure", async () => {
    const cache = new QueryCache();
    const { result } = renderHook(() =>
      useApiQuery("k", async () => Promise.reject(new ApiError("boom", 500)), { cache }),
    );
    await waitFor(() => expect(result.current.error).toBeInstanceOf(ApiError));
    expect(result.current.isLoading).toBe(false);
  });

  it("does nothing for a null key or when disabled", () => {
    const cache = new QueryCache();
    const fetcher = vi.fn(async () => 1);
    renderHook(() => useApiQuery(null, fetcher, { cache }));
    renderHook(() => useApiQuery("k", fetcher, { cache, enabled: false }));
    expect(fetcher).not.toHaveBeenCalled();
  });
});

describe("F0.5.2 prefetch", () => {
  it("a quick hover-out does not fetch; a 120 ms dwell fetches once", () => {
    vi.useFakeTimers();
    const cache = new QueryCache();
    const fetcher = vi.fn(async () => "detail");
    const { result } = renderHook(() => usePrefetchOnIntent("users/42", fetcher, { cache }));

    act(() => result.current.onMouseEnter());
    act(() => vi.advanceTimersByTime(INTENT_DELAY_MS - 40));
    act(() => result.current.onMouseLeave());
    act(() => vi.advanceTimersByTime(500));
    expect(fetcher).not.toHaveBeenCalled();

    act(() => result.current.onFocus());
    act(() => vi.advanceTimersByTime(INTENT_DELAY_MS));
    expect(fetcher).toHaveBeenCalledTimes(1);

    // Hovering again while the prefetch is in flight (or fresh) does not refetch.
    act(() => result.current.onMouseEnter());
    act(() => vi.advanceTimersByTime(INTENT_DELAY_MS));
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("the warmed entry is used by the target page without a second request", async () => {
    const cache = new QueryCache();
    const fetcher = vi.fn(async () => "detail");
    await cache.prefetch("users/42", fetcher);
    const { result } = renderHook(() => useApiQuery("users/42", fetcher, { cache }));
    expect(result.current.data).toBe("detail");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("prefetchNextPage runs in requestIdleCallback", () => {
    const cache = new QueryCache();
    const idle: (() => void)[] = [];
    vi.stubGlobal("requestIdleCallback", (cb: () => void) => idle.push(cb));
    vi.stubGlobal("cancelIdleCallback", vi.fn());
    const fetcher = vi.fn(async () => "page 2");
    prefetchNextPage("logs?page=2", fetcher, { cache });
    expect(fetcher).not.toHaveBeenCalled();
    idle.forEach((cb) => cb());
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("prefetchNextPage falls back to setTimeout and can be cancelled", () => {
    vi.useFakeTimers();
    const cache = new QueryCache();
    vi.stubGlobal("requestIdleCallback", undefined);
    const fetcher = vi.fn(async () => "page 2");
    const cancel = prefetchNextPage("logs?page=2", fetcher, { cache });
    cancel();
    vi.advanceTimersByTime(50);
    expect(fetcher).not.toHaveBeenCalled();
    prefetchNextPage("logs?page=2", fetcher, { cache });
    vi.advanceTimersByTime(50);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});

describe("F0.5.3 optimistic mutation and undo", () => {
  type Todo = string[];

  function setup(mutationFn: (v: string) => Promise<string>, extra: Record<string, unknown> = {}) {
    const cache = new QueryCache();
    cache.setData<Todo>("todos", ["a"]);
    const hook = renderHook(() =>
      useApiMutation<string, string, Todo>({
        mutationFn,
        optimistic: { key: "todos", update: (current, v) => [...(current ?? []), v] },
        cache,
        ...extra,
      }),
    );
    return { cache, hook };
  }

  it("applies the optimistic update, then rolls back on failure", async () => {
    const pending = deferred<string>();
    const { cache, hook } = setup(() => pending.promise);
    let done!: Promise<string | undefined>;
    act(() => {
      done = hook.result.current.mutate("b");
    });
    expect(cache.getData<Todo>("todos")).toEqual(["a", "b"]);
    expect(hook.result.current.isPending).toBe(true);
    await act(async () => {
      pending.reject(new ApiError("nope", 500));
      await done;
    });
    expect(cache.getData<Todo>("todos")).toEqual(["a"]);
    expect(hook.result.current.error).toBeInstanceOf(ApiError);
    expect(hook.result.current.isPending).toBe(false);
  });

  it("undo restores the previous state after success", async () => {
    const { cache, hook } = setup(async () => "ok");
    await act(async () => {
      await hook.result.current.mutate("b");
    });
    expect(cache.getData<Todo>("todos")).toEqual(["a", "b"]);
    expect(hook.result.current.canUndo).toBe(true);
    act(() => {
      expect(hook.result.current.undo()).toBe(true);
    });
    expect(cache.getData<Todo>("todos")).toEqual(["a"]);
    expect(hook.result.current.canUndo).toBe(false);
  });

  it("a 409 rolls back, refetches the key and calls onConflict", async () => {
    const onConflict = vi.fn();
    const { cache, hook } = setup(() => Promise.reject(new ApiError("changed", 409)), { onConflict });
    const refetch = vi.fn(async () => ["a", "server"]);
    renderHook(() => useApiQuery<Todo>("todos", refetch, { cache, staleTime: 60_000 }));
    await act(async () => {
      await hook.result.current.mutate("b");
    });
    expect(onConflict).toHaveBeenCalledWith(
      expect.objectContaining({ vars: "b", refetched: ["todos"] }),
    );
    await waitFor(() => expect(cache.getData<Todo>("todos")).toEqual(["a", "server"]));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("mutateAsync rethrows and a custom rollback receives the previous value", async () => {
    const rollback = vi.fn();
    const { hook } = setup(() => Promise.reject(new Error("x")), { rollback });
    await act(async () => {
      await expect(hook.result.current.mutateAsync("b")).rejects.toThrow("x");
    });
    expect(rollback).toHaveBeenCalledWith(["a"], "b", expect.any(Error));
  });

  it("invalidates the listed prefixes after success", async () => {
    const cache = new QueryCache();
    const list: Fetcher<string> = vi.fn(async () => "list");
    renderHook(() => useApiQuery(["admin", "users"], list, { cache, staleTime: 60_000 }));
    await waitFor(() => expect(list).toHaveBeenCalledTimes(1));
    const { result } = renderHook(() =>
      useApiMutation<string, string>({ mutationFn: async () => "ok", invalidate: ["admin/users"], cache }),
    );
    await act(async () => {
      await result.current.mutate("x");
    });
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
  });
});
