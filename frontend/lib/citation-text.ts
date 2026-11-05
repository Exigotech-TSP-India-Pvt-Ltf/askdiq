/**
 * Visitor-facing citation text: a link ("staging.deployiq.co/our-vision"),
 * never a knowledge-base file name ("our-vision.md").
 */

const FILE_EXT = /\.(?:md|markdown|docx?|txt|pdf)$/i;

/** "https://staging.deployiq.co/our-vision#x" -> "staging.deployiq.co/our-vision". */
export function linkText(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    const path = parsed.pathname.replace(/\/+$/, "");
    return `${parsed.host}${path}`;
  } catch {
    return null;
  }
}

/** A page title for display: a file name becomes words ("our-vision.md" -> "Our Vision"). */
export function displayTitle(title: string | null | undefined): string {
  const raw = (title ?? "").trim();
  if (!raw) return "Source";
  if (!FILE_EXT.test(raw)) return raw;
  const words = raw.replace(FILE_EXT, "").replace(/[-_]+/g, " ").trim();
  if (!words) return "Source";
  return words.replace(/\b\p{L}/gu, (c) => c.toUpperCase());
}
