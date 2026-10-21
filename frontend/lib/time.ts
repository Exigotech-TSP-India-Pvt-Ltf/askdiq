/**
 * Backend timestamps are naive UTC (`datetime.utcnow()`, no trailing
 * 'Z'/offset in the JSON). Without a timezone marker, `new Date(iso)`
 * parses the string as local time instead of UTC, which silently skews
 * every relative/absolute time display by the browser's UTC offset.
 * Force UTC interpretation before handing off to `Date`.
 */
export function parseServerDate(iso: string): Date {
  const hasTimezone = /Z$|[+-]\d{2}:\d{2}$/.test(iso);
  return new Date(hasTimezone ? iso : `${iso}Z`);
}

/** Parses a server timestamp; `null` for empty or unparseable input. */
export function toServerDate(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const date = parseServerDate(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

const MINUTE_MS = 60_000;
const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;
/** Past this age the relative label switches to a calendar date. */
const RELATIVE_MAX_DAYS = 30;

const absoluteFormat = new Intl.DateTimeFormat("en-AU", {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  timeZoneName: "short",
});
const calendarDayFormat = new Intl.DateTimeFormat("en-AU", { day: "numeric", month: "short", year: "numeric" });

/** Local date-time with the viewer's timezone, e.g. "16 Oct 2026, 02:30 pm AEDT". */
export function formatAbsoluteTime(date: Date): string {
  return absoluteFormat.format(date);
}

const shortDateTimeFormat = new Intl.DateTimeFormat("en-AU", {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

/** Compact local date-time for dense grids, e.g. "19 Oct 2026, 09:53" (24 h, no timezone). */
export function formatShortDateTime(date: Date): string {
  return shortDateTimeFormat.format(date);
}

/** Compact relative age ("just now", "5m ago", "3h ago", "2d ago"); a calendar date past 30 days. */
export function formatRelativeTime(date: Date, now: number = Date.now()): string {
  const diff = now - date.getTime();
  const abs = Math.abs(diff);
  if (abs < MINUTE_MS) return "just now";
  if (abs >= RELATIVE_MAX_DAYS * DAY_MS) return calendarDayFormat.format(date);
  const [value, unit] =
    abs < HOUR_MS
      ? [Math.floor(abs / MINUTE_MS), "m"]
      : abs < DAY_MS
        ? [Math.floor(abs / HOUR_MS), "h"]
        : [Math.floor(abs / DAY_MS), "d"];
  return diff >= 0 ? `${value}${unit} ago` : `in ${value}${unit}`;
}
