/**
 * A bar's open time, always labelled UTC (lib/format.ts's rule: an unlabelled
 * time on a trading screen is an ambiguous time). Shared by the chart's
 * crosshair label, the readout and the data table, so all three agree.
 *
 * Built from the UTC fields rather than Intl: ICU versions disagree on the
 * en-GB short month ("Sep" or "Sept") and on punctuation, and a readout that
 * reads differently on two machines is harder to trust and to test.
 */
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"] as const;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"] as const;

const two = (n: number) => String(n).padStart(2, "0");

/** `seconds`: UTC seconds since the epoch (the chart's time unit). "Mon 21 Sep 2026 14:00 UTC". */
export function formatBarTime(seconds: number): string {
  if (!Number.isFinite(seconds)) return "—";
  const d = new Date(seconds * 1_000);
  if (Number.isNaN(d.getTime())) return "—";
  return `${WEEKDAYS[d.getUTCDay()]} ${two(d.getUTCDate())} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()} ${two(d.getUTCHours())}:${two(d.getUTCMinutes())} UTC`;
}
