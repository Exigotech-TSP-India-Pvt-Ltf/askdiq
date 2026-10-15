// Server-only proxy to the FastAPI backend. Keeps API_KEY out of the browser
// bundle — only this route handler (which runs on the server) ever reads it.
import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL = process.env.API_BASE_URL ?? "http://127.0.0.1:8000";
const API_KEY = process.env.API_KEY;
const MAX_BODY_BYTES = 15 * 1024 * 1024; // 15 MB

async function forward(req: NextRequest, path: string[]): Promise<Response> {
  // Path traversal & SSRF hygiene (OWASP A10)
  for (const segment of path) {
    if (segment === ".." || segment.includes("/") || segment.includes("\\")) {
      return NextResponse.json({ detail: "Invalid request path" }, { status: 400 });
    }
  }

  const targetUrl = `${BACKEND_URL}/${path.join("/")}${req.nextUrl.search}`;

  const headers = new Headers();
  const incomingContentType = req.headers.get("content-type");
  if (incomingContentType) {
    headers.set("Content-Type", incomingContentType);
  } else if (req.method !== "GET" && req.method !== "HEAD") {
    headers.set("Content-Type", "application/json");
  }

  if (API_KEY) headers.set("X-API-Key", API_KEY);
  const clientId = req.headers.get("x-client-id");
  if (clientId) headers.set("X-Client-Id", clientId);
  const auth = req.headers.get("authorization");
  if (auth) headers.set("Authorization", auth);

  // Forward client IP for accurate attribution
  const forwardedFor = req.headers.get("x-forwarded-for");
  if (forwardedFor) {
    headers.set("X-Forwarded-For", forwardedFor);
  }

  const hasBody = req.method !== "GET" && req.method !== "HEAD";
  let bodyBuffer: Buffer | undefined;

  if (hasBody) {
    const arrayBuf = await req.arrayBuffer();
    if (arrayBuf.byteLength > MAX_BODY_BYTES) {
      return NextResponse.json({ detail: "Payload exceeds maximum allowed size (15MB)" }, { status: 413 });
    }
    bodyBuffer = Buffer.from(arrayBuf);
  }

  try {
    const res = await fetch(targetUrl, {
      method: req.method,
      headers,
      body: bodyBuffer,
      cache: "no-store",
    });

    if (res.status === 204) {
      return new NextResponse(null, { status: 204 });
    }
    const outHeaders = new Headers();
    const ct = res.headers.get("content-type");
    if (ct) outHeaders.set("Content-Type", ct);
    const cd = res.headers.get("content-disposition");
    if (cd) outHeaders.set("Content-Disposition", cd);

    return new NextResponse(res.body, {
      status: res.status,
      headers: outHeaders,
    });
  } catch (err: any) {
    const code = err?.cause?.code || err?.code || "ECONNREFUSED";
    console.error(`[Proxy] Failed to reach backend at ${targetUrl}: ${err?.message || err}`);
    return NextResponse.json(
      {
        detail: `Backend service is unreachable at ${BACKEND_URL} (${code}). Please ensure the FastAPI backend server is running.`,
      },
      { status: 502 }
    );
  }
}

export async function GET(req: NextRequest, { params }: { params: { path: string[] } }) {
  return forward(req, params.path);
}
export async function POST(req: NextRequest, { params }: { params: { path: string[] } }) {
  return forward(req, params.path);
}
export async function PUT(req: NextRequest, { params }: { params: { path: string[] } }) {
  return forward(req, params.path);
}
export async function PATCH(req: NextRequest, { params }: { params: { path: string[] } }) {
  return forward(req, params.path);
}
export async function DELETE(req: NextRequest, { params }: { params: { path: string[] } }) {
  return forward(req, params.path);
}
