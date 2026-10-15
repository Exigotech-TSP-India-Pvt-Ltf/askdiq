/**
 * Full-page navigation (leaves the SPA: sign-in hand-off, sign out). Kept in
 * its own module so tests can mock it (jsdom does not implement navigation).
 */
export function redirectTo(url: string): void {
  window.location.assign(url);
}
