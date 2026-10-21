/**
 * Knowledge source URLs (OR-7): the client mirror of the server's shared
 * validator (`https` only, no userinfo, no whitespace or control characters,
 * at most 2048 characters, host on the allow-list). The server stays the
 * authority; this only gives instant feedback and guards every `href`.
 *
 * No regex here has nested or overlapping quantifiers (linear time on any
 * input, see the CodeQL polynomial-ReDoS fix in 6bbc919).
 */

export const MAX_SOURCE_URL_CHARS = 2048;
/** Server defaults (`KnowledgeSettingsRead.max_hosts` / `max_host_length`). */
export const MAX_ALLOWED_HOSTS = 50;
export const MAX_HOST_CHARS = 253;
const MAX_LABEL_CHARS = 63;

/** Any whitespace (incl. Unicode spaces) or ASCII control character. */
const UNSAFE_CHARS = /[\s\u0000-\u001f\u007f]/;
/** One DNS label in ASCII (punycode) form; hyphen placement is checked separately. */
const LABEL = /^[a-z0-9-]+$/;

function parseHttps(value: string): URL | null {
  if (!value || value.length > MAX_SOURCE_URL_CHARS || UNSAFE_CHARS.test(value)) return null;
  // Protocol-relative ("//host") and anything without an explicit https:// prefix is refused.
  if (value.slice(0, 8).toLowerCase() !== "https://") return null;
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  if (url.protocol !== "https:" || !url.hostname || url.username || url.password) return null;
  return url;
}

/**
 * `href` guard for every server-provided link: the normalized URL when it is
 * a plain `https:` URL, otherwise `null` (`javascript:`, `data:`, `http:`,
 * protocol-relative, userinfo, control characters, over-long).
 */
export function safeHttpsUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  return parseHttps(value)?.href ?? null;
}

/** "docs.example.com/guide/setup" (host + path, no scheme or trailing slash) for compact display. */
export function displayUrl(value: string): string {
  try {
    const url = new URL(value);
    const path = url.pathname === "/" ? "" : url.pathname;
    return `${url.host}${path}${url.search}${url.hash}`;
  } catch {
    return value;
  }
}

/** Exact host, or a single leading wildcard (`*.example.com` matches its subdomains). */
export function hostAllowed(host: string, allowedHosts: readonly string[] | null | undefined): boolean {
  if (!allowedHosts?.length) return true; // empty list: any https domain
  const h = host.toLowerCase();
  return allowedHosts.some((entry) => {
    const e = entry.toLowerCase();
    if (e.startsWith("*.")) {
      const suffix = e.slice(1);
      return h.length > suffix.length && h.endsWith(suffix);
    }
    return h === e;
  });
}

export type SourceUrlCheck = { ok: true; url: string | null } | { ok: false; error: string };

/**
 * Validates a Source URL as typed. Empty is `{ ok: true, url: null }` unless
 * `required`. `allowedHosts` `null` skips the allow-list (the server checks it).
 */
export function validateSourceUrl(
  raw: string,
  { allowedHosts = null, required = false }: { allowedHosts?: readonly string[] | null; required?: boolean } = {},
): SourceUrlCheck {
  const value = raw.trim();
  if (!value) return required ? { ok: false, error: "A source URL is required." } : { ok: true, url: null };
  if (value.length > MAX_SOURCE_URL_CHARS) {
    return { ok: false, error: `The URL is longer than ${MAX_SOURCE_URL_CHARS} characters.` };
  }
  if (UNSAFE_CHARS.test(value)) return { ok: false, error: "The URL can't contain spaces or control characters." };
  if (value.startsWith("//")) return { ok: false, error: "Enter a full URL starting with https://." };
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return { ok: false, error: "Enter a full URL starting with https://." };
  }
  if (url.protocol !== "https:" || value.slice(0, 8).toLowerCase() !== "https://") {
    return { ok: false, error: "Only https:// links are allowed." };
  }
  if (url.username || url.password) return { ok: false, error: "Remove the user name or password from the URL." };
  if (!url.hostname) return { ok: false, error: "The URL needs a host name." };
  if (!hostAllowed(url.hostname, allowedHosts)) {
    return { ok: false, error: `${url.hostname} is not on the allowed domain list (Settings > Knowledge links).` };
  }
  return { ok: true, url: value };
}

function validLabel(label: string): boolean {
  return (
    label.length > 0 &&
    label.length <= MAX_LABEL_CHARS &&
    LABEL.test(label) &&
    !label.startsWith("-") &&
    !label.endsWith("-")
  );
}

export type HostEntryCheck = { ok: true; host: string } | { ok: false; error: string };

/**
 * Normalizes one allow-list entry: lower-cased, IDNA (punycode) ASCII, a
 * hostname with an optional single leading `*.` wildcard, at most
 * `maxLength` characters.
 */
export function normalizeHostEntry(raw: string, maxLength: number = MAX_HOST_CHARS): HostEntryCheck {
  const value = raw.trim().toLowerCase();
  if (!value) return { ok: false, error: "Enter a domain, e.g. docs.example.com." };
  if (value.length > maxLength) return { ok: false, error: `A domain can be at most ${maxLength} characters.` };
  if (UNSAFE_CHARS.test(value) || /[/:@?#\\[\]%]/.test(value)) {
    return { ok: false, error: "Enter only the domain (no https://, path, port or spaces)." };
  }
  const wildcard = value.startsWith("*.");
  const rest = wildcard ? value.slice(2) : value;
  if (rest.includes("*")) return { ok: false, error: "Only a single leading wildcard is allowed, e.g. *.example.com." };
  let ascii: string;
  try {
    ascii = new URL(`https://${rest}`).hostname;
  } catch {
    return { ok: false, error: `${raw.trim()} is not a valid domain.` };
  }
  const labels = ascii.split(".");
  if (labels.length < 2 || !labels.every(validLabel)) return { ok: false, error: `${raw.trim()} is not a valid domain.` };
  const host = wildcard ? `*.${ascii}` : ascii;
  if (host.length > maxLength) return { ok: false, error: `A domain can be at most ${maxLength} characters.` };
  return { ok: true, host };
}
