/**
 * Security response headers (newplan §12.2; OWASP A02/A05). The nonce-based
 * Content-Security-Policy is set per request in middleware.ts (PR-18), because
 * a static header cannot carry a fresh nonce.
 *
 * @param {boolean} isProduction HSTS is only sent in production (HTTPS).
 * @returns {{ key: string, value: string }[]}
 */
function buildSecurityHeaders(isProduction) {
  const headers = [
    { key: "X-Content-Type-Options", value: "nosniff" },
    { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
    { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
    { key: "X-Frame-Options", value: "DENY" },
    { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  ];
  if (isProduction) {
    headers.push({ key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" });
  }
  return headers;
}

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Do not advertise the framework (X-Powered-By: Next.js).
  poweredByHeader: false,
  // Required for the Docker multi-stage build (frontend/Dockerfile) — bundles
  // only the files needed to run, into .next/standalone.
  output: "standalone",
  experimental: {
    optimizePackageImports: ["lucide-react", "recharts", "motion"],
  },
  // The retired Evaluation page (newplan 2.3). A config redirect answers with a
  // real 307 + Location before the (app) layout starts streaming; the page-level
  // redirect() stays as a fallback.
  async redirects() {
    return [
      { source: "/admin/evaluation", destination: "/admin/evaluation-live", permanent: false },
      { source: "/evaluation", destination: "/admin/evaluation-live", permanent: false },
    ];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: buildSecurityHeaders(process.env.NODE_ENV === "production"),
      },
    ];
  },
};

module.exports = nextConfig;
// Non-enumerable so Next's config validation does not warn about an unknown key.
Object.defineProperty(module.exports, "buildSecurityHeaders", { value: buildSecurityHeaders, enumerable: false });
