// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_BRAND } from "@/lib/brand";
import { BRANDING_REVALIDATE_SECONDS, BRANDING_TIMEOUT_MS, fetchBrand, resetBrandCache } from "@/lib/brand-server";

const STALE_MS = BRANDING_REVALIDATE_SECONDS * 1000 + 1;

function ok(name: string): Response {
  return new Response(JSON.stringify({ brand_name: name }), { status: 200 });
}

/** A backend that never sends headers: the fetch settles only when its signal aborts. */
function hangingFetch() {
  return vi.fn((_url: string, init?: RequestInit) => {
    return new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(init.signal?.reason));
    });
  });
}

let timeoutController: AbortController;

beforeEach(() => {
  resetBrandCache();
  timeoutController = new AbortController();
  vi.spyOn(AbortSignal, "timeout").mockReturnValue(timeoutController.signal);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("fetchBrand bounds every backend call (no 300 s hang)", () => {
  it("asks for a BRANDING_TIMEOUT_MS abort signal and falls back to defaults when it fires", async () => {
    const fetchMock = hangingFetch();
    const pending = fetchBrand(fetchMock as unknown as typeof fetch);
    expect(AbortSignal.timeout).toHaveBeenCalledWith(BRANDING_TIMEOUT_MS);
    timeoutController.abort(new DOMException("timed out", "TimeoutError"));
    await expect(pending).resolves.toEqual(DEFAULT_BRAND);
  });

  it("serves a stale brand at once and never makes the render wait on the refresh", async () => {
    let now = 0;
    const clock = () => now;
    const good = vi.fn().mockResolvedValue(ok("Acme"));
    expect((await fetchBrand(good as unknown as typeof fetch, clock)).productName).toBe("Acme");

    now = STALE_MS;
    const hung = hangingFetch();
    const brand = await fetchBrand(hung as unknown as typeof fetch, clock);
    expect(brand.productName).toBe("Acme");
    expect(hung).toHaveBeenCalledTimes(1);

    // Later renders while that refresh is still pending share it (no pile-up).
    await fetchBrand(hung as unknown as typeof fetch, clock);
    expect(hung).toHaveBeenCalledTimes(1);

    timeoutController.abort();
    await vi.waitFor(async () => {
      // The failed refresh keeps the last good brand and lets the next render retry.
      expect((await fetchBrand(good as unknown as typeof fetch, clock)).productName).toBe("Acme");
      expect(good).toHaveBeenCalledTimes(2);
    });
  });

  it("reuses a fresh brand without calling the backend", async () => {
    const good = vi.fn().mockResolvedValue(ok("Acme"));
    await fetchBrand(good as unknown as typeof fetch, () => 0);
    await fetchBrand(good as unknown as typeof fetch, () => STALE_MS - 2);
    expect(good).toHaveBeenCalledTimes(1);
  });

  it("collapses concurrent cold renders into one backend call", async () => {
    const good = vi.fn().mockResolvedValue(ok("Acme"));
    const brands = await Promise.all([1, 2, 3].map(() => fetchBrand(good as unknown as typeof fetch)));
    expect(good).toHaveBeenCalledTimes(1);
    expect(brands.every((b) => b.productName === "Acme")).toBe(true);
  });
});
