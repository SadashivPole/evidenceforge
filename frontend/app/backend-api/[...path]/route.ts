/**
 * Runtime proxy for backend API traffic.
 *
 * Why this exists: `next.config.mjs` rewrites resolve EVIDENCEFORGE_API_URL at
 * build time. The production container image (standalone output) must honour
 * the environment value per process start instead, so standalone builds use
 * this route handler. Classic `next dev`/`next start` continue to use the
 * build-time rewrite; both paths converge on the same upstream semantics.
 *
 * The handler is deliberately thin: no caching, no cookie mutation, no
 * response rewriting. It forwards method, path, query, and body verbatim and
 * strips hop-by-hop headers.
 */

import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const UPSTREAM_TIMEOUT_MS = 60_000;
const HOP_HEADERS = new Set([
  "connection",
  "content-length",
  "expect",
  "host",
  "keep-alive",
  "transfer-encoding",
  "upgrade",
]);

function upstreamBase(): string | null {
  const raw = process.env.EVIDENCEFORGE_API_URL?.trim();
  if (!raw) return null;
  try {
    const url = new URL(raw);
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    return url.origin + url.pathname.replace(/\/$/, "");
  } catch {
    return null;
  }
}

async function handle(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
): Promise<Response> {
  const base = upstreamBase();
  if (!base) {
    return NextResponse.json(
      { detail: "Backend proxy is not configured (EVIDENCEFORGE_API_URL)" },
      { status: 502 },
    );
  }

  const { path } = await context.params;
  const target = new URL(`${base}/${path.join("/")}${request.nextUrl.search}`);

  const headers = new Headers();
  request.headers.forEach((value, key) => {
    if (!HOP_HEADERS.has(key.toLowerCase())) headers.set(key, value);
  });

  const signal = AbortSignal.timeout(UPSTREAM_TIMEOUT_MS);
  try {
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body:
        request.method === "GET" || request.method === "HEAD"
          ? undefined
          : await request.arrayBuffer(),
      signal,
      redirect: "manual",
    });

    const responseHeaders = new Headers();
    upstream.headers.forEach((value, key) => {
      if (!HOP_HEADERS.has(key.toLowerCase())) responseHeaders.set(key, value);
    });

    return new NextResponse(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: responseHeaders,
    });
  } catch {
    return NextResponse.json(
      { detail: "Backend upstream unreachable" },
      { status: 502 },
    );
  }
}

export {
  handle as DELETE,
  handle as GET,
  handle as HEAD,
  handle as OPTIONS,
  handle as PATCH,
  handle as POST,
  handle as PUT,
};
