/**
 * In-memory router for `vi.mock("next/navigation")` (history with back).
 * Kept free of app imports: the mock factory loads it while app modules
 * (next/link, providers) are still resolving next/navigation.
 */
import { vi } from "vitest";

type Listener = () => void;

const state = { entries: ["/"], index: 0, listeners: new Set<Listener>() };
const notify = () => state.listeners.forEach((l) => l());

export const history = {
  current: () => state.entries[state.index],
  entries: () => [...state.entries],
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
  back() {
    state.index = Math.max(0, state.index - 1);
    notify();
  },
};

/** Current URL's search params. */
export function currentParams(): URLSearchParams {
  return new URLSearchParams(history.current().split("?")[1] ?? "");
}

export async function navigationMock() {
  const { useSyncExternalStore } = await import("react");
  const subscribe = (cb: Listener) => {
    state.listeners.add(cb);
    return () => {
      state.listeners.delete(cb);
    };
  };
  const useUrl = () => useSyncExternalStore(subscribe, history.current);
  return {
    useRouter: () => ({ push: history.push, replace: history.replace, back: history.back, prefetch: vi.fn() }),
    usePathname: () => useUrl().split("?")[0],
    useSearchParams: () => new URLSearchParams(useUrl().split("?")[1] ?? ""),
    useParams: () => {
      const path = useUrl().split("?")[0];
      const last = path.split("/").filter(Boolean).pop() ?? "";
      return { id: last };
    },
  };
}

