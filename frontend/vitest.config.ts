import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
    // GitHub's shared runners are CPU-constrained; under the full suite's
    // parallel worker load, Motion's exit-animation completion (rAF-driven,
    // no real compositor in jsdom) can take longer than the 5s default,
    // intermittently failing waitFor(...not.toBeInTheDocument()) assertions
    // on drawers/dialogs that are otherwise correct (reproduces 0 times in
    // 1429/1429 on an uncontended machine). Matches the existing per-suite
    // headroom already used for AnalyticsRange (30s) and journey tests (15s).
    testTimeout: 15_000,
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./"),
    },
  },
});
