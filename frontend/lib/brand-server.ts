/**
 * Server-only branding fetch, used by app/layout.tsx (layout + generateMetadata).
 * Lives apart from lib/brand.ts so the API key lookup never enters a client bundle.
 */
import { DEFAULT_BRAND, normalizeBrand, type Brand } from "@/lib/brand";

export const BRANDING_REVALIDATE_SECONDS = 300;
const BRANDING_TIMEOUT_MS = 2500;

/**
 * Server-side fetch of `/branding` (public endpoint) with ISR caching.
 * Any failure (backend down at build time, timeout, bad JSON) yields the
 * seeded defaults so the shell always renders.
 */
export async function fetchBrand(fetchImpl: typeof fetch = fetch): Promise<Brand> {
  const base = process.env.API_BASE_URL ?? "http://127.0.0.1:8000";
  const headers = { Accept: "application/json" }; // public endpoint: no API key
  try {
    const res = await fetchImpl(`${base.replace(/\/+$/, "")}/branding`, {
      headers,
      signal: AbortSignal.timeout(BRANDING_TIMEOUT_MS),
      next: { revalidate: BRANDING_REVALIDATE_SECONDS },
    } as RequestInit);
    if (!res.ok) return DEFAULT_BRAND;
    return normalizeBrand(await res.json());
  } catch {
    return DEFAULT_BRAND;
  }
}
