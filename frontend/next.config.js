/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Required for the Docker multi-stage build (frontend/Dockerfile) — bundles
  // only the files needed to run, into .next/standalone.
  output: "standalone",
};

module.exports = nextConfig;
