/**
 * Full-page navigation (leaves the SPA: sign-in hand-off, sign out). Kept in
 * its own module so tests can mock it (jsdom does not implement navigation).
 */
export function redirectTo(url: string, options: { replace?: boolean } = {}): void {
  // `replace` drops the current entry, so Back cannot return to it (sign out).
  if (options.replace) window.location.replace(url);
  else window.location.assign(url);
}
