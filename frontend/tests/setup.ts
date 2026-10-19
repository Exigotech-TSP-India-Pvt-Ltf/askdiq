import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { queryCache } from "@/lib/query-cache";

// The app-wide query cache is a module singleton: each test starts empty
// (cached data from one test must never answer another).
afterEach(() => {
  queryCache.clear();
  try {
    window.sessionStorage.clear();
  } catch {
    // no DOM in this environment
  }
});
