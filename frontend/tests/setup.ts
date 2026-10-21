import "@testing-library/jest-dom/vitest";
import { configure } from "@testing-library/react";
import { afterEach } from "vitest";
import { queryCache } from "@/lib/query-cache";

// findBy*/waitFor default to 1 s, which full-suite load (and slower CI
// runners) can exceed for a mocked fetch -> render; passing tests still
// resolve on the first successful poll, so this only adds headroom.
configure({ asyncUtilTimeout: 4000 });

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
