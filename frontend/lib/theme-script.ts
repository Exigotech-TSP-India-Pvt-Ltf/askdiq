/**
 * Theme preference plumbing shared by the pre-paint script (app/layout.tsx)
 * and ThemeProvider. Kept free of React so it can run before hydration.
 *
 * Three choices (OR-3 profile menu): Light, Dark and System. System follows
 * the OS `prefers-color-scheme` live. A missing or unknown stored value means
 * System; nothing is written until the user picks a theme.
 */

export type ThemeMode = "light" | "dark" | "system";
/** The theme actually shown. */
export type ResolvedTheme = "light" | "dark";

export const THEME_STORAGE_KEY = "askdiq-theme";
export const DARK_QUERY = "(prefers-color-scheme: dark)";
export const THEME_MODES: readonly ThemeMode[] = ["light", "dark", "system"];

export function isThemeMode(value: unknown): value is ThemeMode {
  return value === "light" || value === "dark" || value === "system";
}

/** The stored choice; a missing or unknown value is System. */
export function resolveStoredMode(stored: unknown): ThemeMode {
  return isThemeMode(stored) ? stored : "system";
}

/** The theme a choice shows, given the current OS preference. */
export function resolveTheme(mode: ThemeMode, systemPrefersDark: boolean): ResolvedTheme {
  if (mode === "system") return systemPrefersDark ? "dark" : "light";
  return mode;
}

/** Applies the theme to <html>: the `dark` class and `color-scheme`. */
export function applyTheme(resolved: ResolvedTheme, root: HTMLElement = document.documentElement): void {
  root.classList.toggle("dark", resolved === "dark");
  root.style.colorScheme = resolved;
}

/** JSON for embedding inside an inline <script>: `<`, `>`, `/` and line separators escaped. */
function scriptLiteral(value: string): string {
  return JSON.stringify(value).replace(/[<>/\u2028\u2029]/g, (c) => `\\u${c.charCodeAt(0).toString(16).padStart(4, "0")}`);
}

/**
 * T3: runs inline in <head> before first paint so the theme is applied before
 * any pixel is drawn (no flash). Must stay dependency-free and mirror
 * `resolveStoredMode` + `resolveTheme` + `applyTheme`. The middleware's CSP
 * nonce is passed by app/layout.tsx.
 */
export const THEME_INIT_SCRIPT = `(function(){var r=document.documentElement;var d=false;try{var m=localStorage.getItem(${scriptLiteral(
  THEME_STORAGE_KEY,
)});if(m==="dark"){d=true;}else if(m!=="light"){d=window.matchMedia(${scriptLiteral(
  DARK_QUERY,
)}).matches;}}catch(e){try{d=window.matchMedia(${scriptLiteral(DARK_QUERY)}).matches;}catch(e2){}}r.classList.toggle("dark",d);r.style.colorScheme=d?"dark":"light";})();`;
