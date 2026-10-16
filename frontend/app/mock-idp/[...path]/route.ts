// Development-only forwarder for the backend's mock Entra IdP (AUTH_DEV_MOCK).
// With the mock on, the authorize URL points at APP_BASE_URL/mock-idp/..., so
// the browser (authorize form) and the backend (token, JWKS) reach the mock
// through port 3000. Off (404) unless AUTH_DEV_MOCK=true is set for this
// server; the backend itself only mounts the mock when ENV=development.
import { NextResponse, type NextRequest } from "next/server";
import { backendTimeoutMs, backendUrl } from "@/lib/server/backend";

export const dynamic = "force-dynamic";

const MAX_BODY_BYTES = 64 * 1024;
const SEGMENT_RE = /^[A-Za-z0-9._-]+$/;
const PASS_HEADERS = ["content-type", "location", "cache-control"];

function enabled(): boolean {
  return process.env.AUTH_DEV_MOCK === "true";
}

async function forward(req: NextRequest, path: string[]): Promise<Response> {
  if (!enabled()) return NextResponse.json({ detail: "Not Found" }, { status: 404 });
  if (!path.every((segment) => segment !== ".." && SEGMENT_RE.test(segment))) {
    return NextResponse.json({ detail: "Invalid request path" }, { status: 400 });
  }

  const headers = new Headers();
  const contentType = req.headers.get("content-type");
  if (contentType) headers.set("Content-Type", contentType);
  const forwardedFor = req.headers.get("x-forwarded-for");
  if (forwardedFor) headers.set("X-Forwarded-For", forwardedFor);

  let body: ArrayBuffer | undefined;
  if (req.method === "POST") {
    body = await req.arrayBuffer();
    if (body.byteLength > MAX_BODY_BYTES) return NextResponse.json({ detail: "Payload too large" }, { status: 413 });
  }

  try {
    const res = await fetch(`${backendUrl()}/mock-idp/${path.join("/")}${req.nextUrl.search}`, {
      method: req.method,
      headers,
      body,
      redirect: "manual",
      cache: "no-store",
      signal: AbortSignal.timeout(backendTimeoutMs(false)),
    });
    const out = new Headers();
    for (const name of PASS_HEADERS) {
      const value = res.headers.get(name);
      if (value) out.set(name, value);
    }
    return new NextResponse(res.body, { status: res.status, headers: out });
  } catch {
    return NextResponse.json({ detail: "Mock identity provider unreachable" }, { status: 502 });
  }
}

export async function GET(req: NextRequest, { params }: { params: { path: string[] } }) {
  return forward(req, params.path);
}
export async function POST(req: NextRequest, { params }: { params: { path: string[] } }) {
  return forward(req, params.path);
}
