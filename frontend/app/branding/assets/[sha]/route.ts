// Public passthrough for content-addressed brand images.
//
// `GET /branding` returns relative asset URLs (`/branding/assets/{sha}`), and
// the browser resolves them against this Next origin, so this route serves
// that exact path from the FastAPI backend. It is deliberately narrow: GET
// only, the id must be a sha256 hex digest (no traversal or arbitrary
// backend paths), no API key or credentials are forwarded (the backend route
// is public), and only the caching and safety headers the backend sets are
// passed on (immutable Cache-Control, ETag, the SVG Content-Security-Policy
// and nosniff).
import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL = (process.env.API_BASE_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
const SHA256_RE = /^[0-9a-f]{64}$/;
const PASSTHROUGH_HEADERS = [
  "content-type",
  "content-length",
  "etag",
  "cache-control",
  "content-security-policy",
  "x-content-type-options",
] as const;
const UPSTREAM_TIMEOUT_MS = 5000;

export async function GET(req: NextRequest, { params }: { params: { sha: string } }): Promise<Response> {
  const { sha } = params;
  if (!SHA256_RE.test(sha)) {
    return NextResponse.json({ detail: "Brand asset not found." }, { status: 404 });
  }

  const headers = new Headers({ Accept: "image/*" });
  const ifNoneMatch = req.headers.get("if-none-match");
  if (ifNoneMatch) headers.set("If-None-Match", ifNoneMatch);

  let upstream: Response;
  try {
    upstream = await fetch(`${BACKEND_URL}/branding/assets/${sha}`, {
      headers,
      cache: "no-store",
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
  } catch {
    return NextResponse.json({ detail: "Brand asset is unavailable." }, { status: 502 });
  }

  const out = new Headers();
  for (const name of PASSTHROUGH_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) out.set(name, value);
  }
  out.set("X-Content-Type-Options", "nosniff");

  if (upstream.status === 304) return new Response(null, { status: 304, headers: out });
  if (!upstream.ok) {
    return NextResponse.json(
      { detail: upstream.status === 404 ? "Brand asset not found." : "Brand asset is unavailable." },
      { status: upstream.status === 404 ? 404 : 502 },
    );
  }
  return new Response(upstream.body, { status: 200, headers: out });
}
