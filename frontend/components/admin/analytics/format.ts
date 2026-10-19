/** Number and date formatting for the Analytics page (the HTML used en-AU). */
const LOCALE = "en-AU";
const integer = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 0 });
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function fmtInt(value: number): string {
  return integer.format(value);
}

/** `48.11` -> "48%"; null -> "n/a" (same as the HTML's KPI cards). */
export function fmtPct(value: number | null | undefined): string {
  return value === null || value === undefined ? "n/a" : `${value.toFixed(0)}%`;
}

export function fmtDecimal(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined ? "n/a" : value.toFixed(digits);
}

/** "2026-05-15" -> "15 May" (the HTML's `dlabel()`). */
export function dayLabel(day: string): string {
  const [, m, d] = day.split("-");
  const month = MONTHS[Number(m) - 1];
  return month ? `${Number(d)} ${month}` : day;
}

/** ISO timestamp -> "2026-05-15". */
export function isoDay(ts: string): string {
  return ts.slice(0, 10);
}

/** ISO timestamp -> "12:46" (wall-clock part as returned by the API). */
export function clock(ts: string): string {
  return ts.slice(11, 16);
}

/** First block of a UUID, for compact display; the full id is in the link. */
export function shortId(id: string): string {
  return id.split("-")[0] ?? id;
}

const pad2 = (n: number) => String(n).padStart(2, "0");

/** 9 -> "09:00-10:00" (the hour-of-day filter). */
export function hourRangeLabel(hour: number): string {
  return `${pad2(hour)}:00–${pad2((hour + 1) % 24)}:00`;
}
