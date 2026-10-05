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
