/**
 * Server-only branding fetch, used by app/layout.tsx (layout + generateMetadata).
 * Lives apart from lib/brand.ts so the API key lookup never enters a client bundle.
 *
 * Cached in this process, not in Next's fetch data cache (`next.revalidate`):
 * Next 14 refreshes a stale data-cache entry in the background *without* the
 * request's AbortSignal and keeps the page response open until that refresh
 * settles. With a hung backend every page render then stayed open until
 * undici's 300 s headers timeout ("fetch failed" / "failed to pipe response").
 * Here every backend call is bounded and a render never waits on a refresh.
 */
import { DEFAULT_BRAND, normalizeBrand, type Brand } from "@/lib/brand";

export const BRANDING_REVALIDATE_SECONDS = 300;
export const BRANDING_TIMEOUT_MS = 2500;
const MS_PER_SECOND = 1000;

interface CachedBrand {
  brand: Brand;
  expiresAt: number;
}

let cached: CachedBrand | null = null;
let inflight: Promise<Brand | null> | null = null;

/** Drops the process cache (tests). */
export function resetBrandCache(): void {
  cached = null;
  inflight = null;
}

/** One bounded `/branding` call; null on any failure (down, timeout, non-2xx, bad JSON). */
async function loadBrand(fetchImpl: typeof fetch): Promise<Brand | null> {
  const base = process.env.API_BASE_URL ?? "http://127.0.0.1:8000";
  try {
    const res = await fetchImpl(`${base.replace(/\/+$/, "")}/branding`, {
      headers: { Accept: "application/json" }, // public endpoint: no API key
      cache: "no-store",
      signal: AbortSignal.timeout(BRANDING_TIMEOUT_MS),
    });
    if (!res.ok) return null;
    return normalizeBrand(await res.json());
  } catch {
    return null;
  }
}

/** Single-flight refresh; a success is kept for BRANDING_REVALIDATE_SECONDS. */
function refreshBrand(fetchImpl: typeof fetch, now: () => number): Promise<Brand | null> {
  if (inflight) return inflight;
  const run = loadBrand(fetchImpl)
    .then((brand) => {
      if (brand) cached = { brand, expiresAt: now() + BRANDING_REVALIDATE_SECONDS * MS_PER_SECOND };
      return brand;
    })
    .finally(() => {
      if (inflight === run) inflight = null;
    });
  inflight = run;
  return run;
}

/**
 * Branding for the server layout. Fresh cache: served as is. Stale cache:
 * served at once while one bounded refresh runs in the background. No cache:
 * waits at most BRANDING_TIMEOUT_MS. A failure yields the last good brand or
 * the seeded defaults, so the shell always renders.
 */
export async function fetchBrand(fetchImpl: typeof fetch = fetch, now: () => number = Date.now): Promise<Brand> {
  if (cached && cached.expiresAt > now()) return cached.brand;
  if (cached) {
    void refreshBrand(fetchImpl, now);
    return cached.brand;
  }
  return (await refreshBrand(fetchImpl, now)) ?? DEFAULT_BRAND;
}
