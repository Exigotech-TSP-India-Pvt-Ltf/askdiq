/**
 * Allow-listed sign-in error codes. The route handlers
 * redirect to `/login?error=<code>`; the login page only ever renders the
 * fixed message for a known code, never the raw query value.
 */
export const LOGIN_ERROR_MESSAGES = {
  state_mismatch: "Your sign-in session didn't match. Please start again.",
  expired: "Your sign-in link expired or was already used. Please start again.",
  signin_failed: "We couldn't verify your Microsoft sign-in. Please try again.",
  identity_conflict: "This email is linked to a different Microsoft account. Contact your administrator.",
  rate_limited: "Too many sign-in attempts. Please wait a minute and try again.",
  idp_unavailable: "Microsoft sign-in is unavailable right now. Please try again shortly.",
  idp_denied: "Sign-in was cancelled or denied at Microsoft.",
  backend_unavailable: "The service is unavailable right now. Please try again shortly.",
  session_expired: "Your session has ended. Please sign in again.",
} as const;

export type LoginErrorCode = keyof typeof LOGIN_ERROR_MESSAGES;

export const GENERIC_LOGIN_ERROR = "Sign-in didn't complete. Please try again.";

export function isLoginErrorCode(value: unknown): value is LoginErrorCode {
  return typeof value === "string" && Object.prototype.hasOwnProperty.call(LOGIN_ERROR_MESSAGES, value);
}

/** Fixed message for `code`; any unknown value gets the generic message. */
export function loginErrorMessage(code: string | null | undefined): string | null {
  if (!code) return null;
  return isLoginErrorCode(code) ? LOGIN_ERROR_MESSAGES[code] : GENERIC_LOGIN_ERROR;
}

/** Backend status of a failed sign-in step -> error code. */
export function loginErrorForStatus(status: number): LoginErrorCode {
  if (status === 400) return "expired";
  if (status === 401) return "signin_failed";
  if (status === 403) return "identity_conflict";
  if (status === 429) return "rate_limited";
  return "idp_unavailable";
}
