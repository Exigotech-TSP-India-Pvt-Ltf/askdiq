/**
 * AskDIQ floating chat (components/askdiq): storage keys, the once-per-session
 * greeting rule and the "small screen" breakpoint. Storage access never throws
 * (private mode / blocked site data just means the greeting shows again).
 */
import { useSyncExternalStore } from "react";
import { readStorage, writeStorage } from "@/lib/storage";

/** localStorage: set once the greeting was dismissed (never shown again). */
export const GREETING_DISMISSED_KEY = "askdiq.greeting.dismissed";
/** sessionStorage: set once the greeting was shown in this browser session. */
export const GREETING_SEEN_KEY = "askdiq.greeting.seen";

/** Routes that already render the full conversation, so the launcher stays hidden. */
export const CHAT_ROUTE = "/chat";

/** Under this width the drawer is full-screen and modal (Tailwind `sm`). */
export const SMALL_SCREEN_QUERY = "(max-width: 639px)";

function readSession(key: string): string | null {
  try {
    return window.sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeSession(key: string, value: string): void {
  try {
    window.sessionStorage.setItem(key, value);
  } catch {
    // Blocked storage: the greeting may show again next page load.
  }
}

/** True when the greeting should appear: never dismissed and not yet shown this session. */
export function shouldShowGreeting(): boolean {
  return readStorage(GREETING_DISMISSED_KEY) === null && readSession(GREETING_SEEN_KEY) === null;
}

export function markGreetingSeen(): void {
  writeSession(GREETING_SEEN_KEY, "1");
}

export function dismissGreeting(): void {
  writeStorage(GREETING_DISMISSED_KEY, "1");
  markGreetingSeen();
}

export function isChatRoute(pathname: string | null): boolean {
  return pathname === CHAT_ROUTE || Boolean(pathname?.startsWith(`${CHAT_ROUTE}/`));
}

function mediaQuery(query: string): MediaQueryList | null {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return null;
  return window.matchMedia(query);
}

/** Live media query match (false on the server and where matchMedia is missing). */
export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (onChange) => {
      const mq = mediaQuery(query);
      mq?.addEventListener?.("change", onChange);
      return () => mq?.removeEventListener?.("change", onChange);
    },
    () => mediaQuery(query)?.matches ?? false,
    () => false,
  );
}
