/**
 * Theme preference plumbing shared by the pre-paint script (app/layout.tsx)
 * and ThemeProvider. Kept free of React so it can run before hydration.
 *
 * Two themes only: light ("white") and dark ("black"). There is no "system"
 * mode. Migration: a missing, unknown or legacy "system" value is resolved
 * ONCE to the OS preference at that moment and persisted as an explicit
 * "light" / "dark", so it never changes on its own afterwards.
 */

export type ThemeMode = "light" | "dark";
/** Same two values as ThemeMode; kept as a separate name for existing callers. */
export type ResolvedTheme = ThemeMode;

export const THEME_STORAGE_KEY = "askdiq-theme";
export const DARK_QUERY = "(prefers-color-scheme: dark)";

export function isThemeMode(value: unknown): value is ThemeMode {
  return value === "light" || value === "dark";
}

/** The stored mode, or the OS preference for a missing / legacy ("system") / unknown value. */
export function resolveStoredMode(stored: unknown, systemPrefersDark: boolean): ThemeMode {
  if (isThemeMode(stored)) return stored;
  return systemPrefersDark ? "dark" : "light";
}

/** Applies the theme to <html>: the `dark` class and `color-scheme`. */
export function applyTheme(resolved: ThemeMode, root: HTMLElement = document.documentElement): void {
  root.classList.toggle("dark", resolved === "dark");
  root.style.colorScheme = resolved;
}

/**
 * T3: runs inline in <head> before first paint so the theme is applied before
 * any pixel is drawn (no flash). Must stay dependency-free and mirror
 * `resolveStoredMode` + `applyTheme`; it also persists the one-time migration.
 * The middleware's CSP nonce is passed by app/layout.tsx.
 */
/** JSON for embedding inside an inline <script>: `<`, `>`, `/` and line separators escaped. */
function scriptLiteral(value: string): string {
  return JSON.stringify(value).replace(/[<>/\u2028\u2029]/g, (c) => `\\u${c.charCodeAt(0).toString(16).padStart(4, "0")}`);
}

export const THEME_INIT_SCRIPT = `(function(){var r=document.documentElement;var d=false;try{var k=${scriptLiteral(
  THEME_STORAGE_KEY,
)};var m=localStorage.getItem(k);if(m!=="light"&&m!=="dark"){m=window.matchMedia(${scriptLiteral(
  DARK_QUERY,
)}).matches?"dark":"light";try{localStorage.setItem(k,m);}catch(e){}}d=m==="dark";}catch(e){}r.classList.toggle("dark",d);r.style.colorScheme=d?"dark":"light";})();`;
