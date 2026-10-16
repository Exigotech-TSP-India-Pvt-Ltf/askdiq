import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import {
  AUTO_REFRESH_MS,
  STREAM_BACKOFF,
  backoffDelay,
  useAutoRefresh,
  useGovernanceStream,
} from "@/lib/use-governance-stream";

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  closed = false;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  listeners = new Map<string, ((e: MessageEvent<string>) => void)[]>();
  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, cb: (e: MessageEvent<string>) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), cb]);
  }
  close() {
    this.closed = true;
  }
  open() {
    this.onopen?.();
  }
  fail() {
    this.onerror?.();
  }
  emit(type: string, data: unknown) {
    for (const cb of this.listeners.get(type) ?? []) cb({ data: JSON.stringify(data) } as MessageEvent<string>);
  }
}

const latest = () => FakeEventSource.instances[FakeEventSource.instances.length - 1];
const open = () => FakeEventSource.instances.filter((s) => !s.closed);

function setVisibility(state: "visible" | "hidden") {
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
  act(() => {
    document.dispatchEvent(new Event("visibilitychange"));
  });
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
  vi.useFakeTimers();
  setVisibility("visible");
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  setVisibility("visible");
});

describe("backoffDelay", () => {
  it("doubles from 1 s and caps at 30 s", () => {
    expect([0, 1, 2, 3, 4, 5, 6, 10].map(backoffDelay)).toEqual([
      1000, 2000, 4000, 8000, 16000, 30000, 30000, 30000,
    ]);
    expect(STREAM_BACKOFF.maxMs).toBe(30_000);
  });
});

describe("useGovernanceStream", () => {
  it("connects to the audience stream and dispatches parsed events", () => {
    const onEvent = vi.fn();
    const { result } = renderHook(() => useGovernanceStream({ audience: "testing", onEvent }));
    expect(latest().url).toBe("/api/proxy/admin/governance/stream?audience=testing");
    expect(result.current.status).toBe("connecting");
    act(() => latest().open());
    expect(result.current.status).toBe("live");
    act(() => latest().emit("incident", { id: "a1", level: "CRITICAL" }));
    expect(onEvent).toHaveBeenCalledWith({ type: "incident", data: { id: "a1", level: "CRITICAL" } });
    expect(result.current.lastEventAt).toBeGreaterThan(0);
  });

  it("reconnects with exponential backoff and resets after a successful open", () => {
    const { result } = renderHook(() => useGovernanceStream({ audience: "overview", onEvent: vi.fn() }));
    expect(FakeEventSource.instances).toHaveLength(1);

    act(() => latest().fail());
    expect(result.current.status).toBe("reconnecting");
    expect(FakeEventSource.instances[0].closed).toBe(true);
    act(() => vi.advanceTimersByTime(999));
    expect(FakeEventSource.instances).toHaveLength(1);
    act(() => vi.advanceTimersByTime(1));
    expect(FakeEventSource.instances).toHaveLength(2);

    act(() => latest().fail());
    act(() => vi.advanceTimersByTime(1999));
    expect(FakeEventSource.instances).toHaveLength(2);
    act(() => vi.advanceTimersByTime(1));
    expect(FakeEventSource.instances).toHaveLength(3);
    expect(result.current.attempt).toBe(2);

    act(() => latest().open());
    expect(result.current.status).toBe("live");
    expect(result.current.attempt).toBe(0);
    act(() => latest().fail());
    act(() => vi.advanceTimersByTime(1000));
    expect(FakeEventSource.instances).toHaveLength(4);
    expect(open()).toHaveLength(1);
  });

  it("closes while the tab is hidden and reconnects when visible", () => {
    const { result } = renderHook(() => useGovernanceStream({ audience: "overview", onEvent: vi.fn() }));
    act(() => latest().open());
    setVisibility("hidden");
    expect(result.current.status).toBe("paused");
    expect(open()).toHaveLength(0);
    act(() => vi.advanceTimersByTime(60_000));
    expect(FakeEventSource.instances).toHaveLength(1);

    setVisibility("visible");
    expect(FakeEventSource.instances).toHaveLength(2);
    expect(open()).toHaveLength(1);
  });

  it("cancels a pending reconnect when hidden and closes on unmount", () => {
    const { unmount } = renderHook(() => useGovernanceStream({ audience: "overview", onEvent: vi.fn() }));
    act(() => latest().fail());
    setVisibility("hidden");
    act(() => vi.advanceTimersByTime(10_000));
    expect(FakeEventSource.instances).toHaveLength(1);
    setVisibility("visible");
    expect(open()).toHaveLength(1);
    unmount();
    expect(open()).toHaveLength(0);
  });

  it("reports unsupported when EventSource is missing", () => {
    vi.stubGlobal("EventSource", undefined);
    const { result } = renderHook(() => useGovernanceStream({ audience: "overview", onEvent: vi.fn() }));
    expect(result.current.status).toBe("unsupported");
  });
});

describe("useAutoRefresh (G13)", () => {
  it("refreshes on an interval only while enabled and visible", () => {
    const refresh = vi.fn();
    const { rerender, result } = renderHook(({ on }) => useAutoRefresh(on, refresh), { initialProps: { on: false } });
    act(() => vi.advanceTimersByTime(AUTO_REFRESH_MS * 2));
    expect(refresh).not.toHaveBeenCalled();

    rerender({ on: true });
    expect(result.current).toBe(true);
    act(() => vi.advanceTimersByTime(AUTO_REFRESH_MS));
    expect(refresh).toHaveBeenCalledTimes(1);

    setVisibility("hidden");
    expect(result.current).toBe(false);
    act(() => vi.advanceTimersByTime(AUTO_REFRESH_MS * 3));
    expect(refresh).toHaveBeenCalledTimes(1);
  });
});
