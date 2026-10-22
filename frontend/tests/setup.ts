import "@testing-library/jest-dom/vitest";
import { configure } from "@testing-library/react";
import { afterEach } from "vitest";
import { queryCache } from "@/lib/query-cache";
// `MotionProvider` (components/providers/MotionProvider.tsx) only lets
// `AnimatePresence` run an exit animation to completion once Motion's
// feature bundle has loaded via `<LazyMotion features={() => import(...)}>`
// -- a dynamic `import()` resolved inside a `useEffect` that fires after
// mount (framer-motion's `LazyMotion`, `isLazyBundle` branch). Locally that
// import resolves in well under a millisecond (module already compiled from
// earlier test files), so every one of 1429 tests passes every time. Under
// GitHub's CPU-constrained, highly parallel CI runners, the *first* time
// this module needs transforming/compiling, that one-time cost competes
// with every other worker's test file for the same few vCPUs; if a
// dialog/drawer's exit animation starts before it resolves, the
// exit-complete callback can be lost entirely, and the dialog never leaves
// the DOM -- `waitFor(() => ...not.toBeInTheDocument())` then hangs until
// the test times out. That matches every observed symptom: a different
// random subset of AnimatePresence-using tests fails each CI run, it is
// unreproducible locally at any load, and raising `testTimeout` from 5s to
// 15s (444a3a1) did not help, because this is a dropped callback, not a
// slow one.
//
// The fix is not to change *how* or *when* Motion loads (an earlier attempt
// to force synchronous loading broke `useFocusTrap` tests that rely on real
// mount/animation timing, and broke `MascotLoader`/`ToastProvider` tests
// that rely on the original async timing lining up with their
// `vi.useFakeTimers()` windows and infinite-repeat animations) -- it's to
// make sure the one-time compile for this module has already happened,
// every time, before any test's dynamic `import()` needs it, so that
// `import()` always resolves from Vitest's module cache instead of racing a
// fresh compile under CI contention. Every other Motion behaviour
// (AnimatePresence, variants, durations, the lazy-loading control flow
// itself) is untouched.
import "@/lib/motion-features";

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
