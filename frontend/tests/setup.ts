import "@testing-library/jest-dom/vitest";
import { configure } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import React from "react";
import { LazyMotion, MotionConfig, domMax } from "motion/react";
import { queryCache } from "@/lib/query-cache";

// `MotionProvider` (components/providers/MotionProvider.tsx) uses
// `<LazyMotion features={() => import("@/lib/motion-features")...}>`: on
// *every* mount, `LazyMotion` starts in an "unloaded" state and only
// switches to the real animation engine once that import's promise settles
// via its own effect -- a genuine async gap on every single render, not
// something a warmed module cache can remove (two earlier attempts here,
// 444a3a1 and 6aca716, both assumed preloading the module was enough; it
// wasn't -- confirmed by a real CI run still losing exit-animation
// completion afterwards). If a dialog's exit starts in that window, the
// exit-complete callback that AnimatePresence needs to actually unmount it
// can be dropped entirely, not just delayed -- matching every symptom seen
// on CI: a different AnimatePresence-using dialog fails each run, it never
// reproduces locally, and raising `testTimeout` (444a3a1) didn't help.
//
// The fix: give `LazyMotion` the real feature bundle directly
// (`features={domMax}`, not a function) so it is synchronously "loaded" on
// first render -- no promise, no effect, no async gap, ever. This is
// Framer Motion's own supported non-code-split form (bundle size doesn't
// matter in tests); everything else (`AnimatePresence`, variants,
// `MotionConfig`, durations) is the real library, unmocked.
//
// The mock factory itself must stay synchronous (no `await import(...)`
// inside it): an async factory adds one more microtask before React mounts
// the real children, which is enough to desync tests that advance fake
// timers immediately after `render()` (an effect that hasn't committed yet
// never sees that advance) -- `MascotLoader`'s rotating-status test hit
// exactly this.
vi.mock("@/components/providers/MotionProvider", () => ({
  MotionProvider({ children }: { children: React.ReactNode }) {
    return React.createElement(
      LazyMotion,
      { features: domMax, strict: true },
      React.createElement(MotionConfig, { reducedMotion: "user" }, children),
    );
  },
}));

// `MotionConfig reducedMotion="user"` (set above) is Framer Motion's own
// mechanism for honouring `prefers-reduced-motion: reduce` -- independent
// of this app's own `reduceVariants` transform-stripping (lib/motion.ts).
// Defaulting jsdom's `matchMedia` to report that preference (tests that
// stub their own `matchMedia` are unaffected; their override wins) removes
// one more source of animation-timing dependence from tests that don't
// care about it either way. Note it only strips *transforms*: an
// opacity-based crossfade's `transition` duration is untouched, so a test
// that waits on one (MascotLoader, ToastProvider) still needs real
// wall-clock headroom, not a shorter animation -- see their widened
// `waitFor` timeouts.
if (typeof window !== "undefined" && typeof window.matchMedia !== "function") {
  window.matchMedia = ((query: string) => ({
    matches: query.includes("prefers-reduced-motion"),
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as typeof window.matchMedia;
}

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
