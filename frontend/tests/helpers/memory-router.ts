/**
 * In-memory `next/navigation` for tests: `vi.mock("next/navigation", () =>
 * import("../helpers/memory-router").then((m) => m.navigationMock))`.
 * The test and the mocked module share this instance, so tests can read and
 * set the URL.
 */
import { useSyncExternalStore } from "react";
import { vi } from "vitest";

const state = { entries: ["/"], index: 0, listeners: new Set<() => void>() };
const notify = () => state.listeners.forEach((listener) => listener());

export const memoryRouter = {
  current: () => state.entries[state.index],
  params: () => new URLSearchParams(state.entries[state.index].split("?")[1] ?? ""),
  reset(url: string) {
    state.entries = [url];
    state.index = 0;
    notify();
  },
  push: vi.fn((url: string) => {
    state.entries = [...state.entries.slice(0, state.index + 1), url];
    state.index += 1;
    notify();
  }),
  replace: vi.fn((url: string) => {
    state.entries[state.index] = url;
    notify();
  }),
};

const subscribe = (cb: () => void) => {
  state.listeners.add(cb);
  return () => {
    state.listeners.delete(cb);
  };
};
const useUrl = () => useSyncExternalStore(subscribe, memoryRouter.current);
const router = { push: memoryRouter.push, replace: memoryRouter.replace, prefetch: vi.fn(), back: vi.fn(), forward: vi.fn(), refresh: vi.fn() };

export const navigationMock = {
  useRouter: () => router,
  usePathname: () => useUrl().split("?")[0],
  useSearchParams: () => new URLSearchParams(useUrl().split("?")[1] ?? ""),
  redirect: vi.fn(),
};
