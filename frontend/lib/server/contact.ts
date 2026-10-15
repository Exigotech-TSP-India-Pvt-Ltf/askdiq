/**
 * `ACCESS_CONTACT_EMAIL` for pages rendered before a session exists (login,
 * suspended). Server-only. After sign-in the value comes from `/auth/me`.
 */
const EMAIL_RE = /^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,24}$/;

export function isContactEmail(value: unknown): value is string {
  return typeof value === "string" && EMAIL_RE.test(value);
}

export function accessContactEmail(): string | null {
  const value = process.env.ACCESS_CONTACT_EMAIL?.trim();
  return isContactEmail(value) ? value : null;
}
