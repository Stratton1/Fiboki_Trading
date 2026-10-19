/**
 * A bar's open time, always labelled with its zone (lib/format.ts's rule: an
 * unlabelled time on a trading screen is an ambiguous time). Shared by the
 * chart's crosshair label, the readout, the data window and the data table,
 * so all of them agree.
 *
 * UTC is built from the UTC fields rather than Intl: ICU versions disagree on
 * the en-GB short month ("Sep" or "Sept") and on punctuation, and a readout
 * that reads differently on two machines is harder to trust and to test.
 * Another display zone (Wave 2B time-zone selector) takes only the NUMERIC
 * fields from Intl and assembles the same shape. Display only: every time is
 * stored, sent and compared in UTC.
 */
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"] as const;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"] as const;

const two = (n: number) => String(n).padStart(2, "0");

/** The display zones the chart offers. "UTC" is the default and the storage zone. */
export const TIME_ZONES = [
  { id: "UTC", label: "UTC" },
  { id: "Europe/London", label: "London" },
  { id: "America/New_York", label: "New York" },
  { id: "Asia/Tokyo", label: "Tokyo" },
  { id: "Australia/Sydney", label: "Sydney" },
] as const;
export type TimeZoneId = (typeof TIME_ZONES)[number]["id"];

export function isTimeZoneId(value: string | null | undefined): value is TimeZoneId {
  return TIME_ZONES.some((z) => z.id === value);
}

interface Fields {
  weekday: number;
  day: number;
  month: number;
  year: number;
  hour: number;
  minute: number;
  zone: string;
}

const formatters = new Map<string, Intl.DateTimeFormat>();

function fieldsIn(d: Date, tz: TimeZoneId): Fields {
  if (tz === "UTC") {
    return {
      weekday: d.getUTCDay(),
      day: d.getUTCDate(),
      month: d.getUTCMonth(),
      year: d.getUTCFullYear(),
      hour: d.getUTCHours(),
      minute: d.getUTCMinutes(),
      zone: "UTC",
    };
  }
  let fmt = formatters.get(tz);
  if (!fmt) {
    fmt = new Intl.DateTimeFormat("en-US", {
      timeZone: tz,
      year: "numeric",
      month: "numeric",
      day: "numeric",
      hour: "numeric",
      minute: "numeric",
      hourCycle: "h23",
      timeZoneName: "short",
    });
    formatters.set(tz, fmt);
  }
  const parts = Object.fromEntries(fmt.formatToParts(d).map((p) => [p.type, p.value]));
  const year = Number(parts.year);
  const month = Number(parts.month) - 1;
  const day = Number(parts.day);
  // The weekday of the zone's calendar date (no locale strings involved).
  const weekday = new Date(Date.UTC(year, month, day)).getUTCDay();
  return {
    weekday,
    day,
    month,
    year,
    hour: Number(parts.hour) % 24,
    minute: Number(parts.minute),
    zone: parts.timeZoneName ?? tz,
  };
}

/** `seconds`: UTC seconds since the epoch (the chart's time unit). "Mon 21 Sep 2026 14:00 UTC". */
export function formatBarTime(seconds: number, tz: TimeZoneId = "UTC"): string {
  if (!Number.isFinite(seconds)) return "—";
  const d = new Date(seconds * 1_000);
  if (Number.isNaN(d.getTime())) return "—";
  const f = fieldsIn(d, tz);
  return `${WEEKDAYS[f.weekday]} ${two(f.day)} ${MONTHS[f.month]} ${f.year} ${two(f.hour)}:${two(f.minute)} ${f.zone}`;
}

/**
 * A time-axis tick in the display zone. `kind` is the library's TickMarkType
 * (0 year, 1 month, 2 day of month, 3 time, 4 time with seconds).
 */
export function formatTick(seconds: number, kind: number, tz: TimeZoneId = "UTC"): string {
  const d = new Date(seconds * 1_000);
  if (Number.isNaN(d.getTime())) return "";
  const f = fieldsIn(d, tz);
  if (kind === 0) return String(f.year);
  if (kind === 1) return MONTHS[f.month] ?? "";
  if (kind === 2) return `${f.day} ${MONTHS[f.month]}`;
  return `${two(f.hour)}:${two(f.minute)}`;
}

/** The ISO instant for a `datetime-local` value read in a display zone. */
export function zonedInputToIso(value: string, tz: TimeZoneId): string | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(value);
  if (!m) return null;
  const [, y, mo, d, h, mi] = m.map(Number) as [number, number, number, number, number, number];
  const asUtc = Date.UTC(y, mo - 1, d, h, mi);
  if (tz === "UTC") return new Date(asUtc).toISOString().replace(".000Z", "Z");
  // The zone's offset at that wall time: format the UTC guess in the zone and
  // correct by the difference (twice, for a guess that crossed a DST change).
  let guess = asUtc;
  for (let i = 0; i < 2; i += 1) {
    const f = fieldsIn(new Date(guess), tz);
    const wall = Date.UTC(f.year, f.month, f.day, f.hour, f.minute);
    guess += asUtc - wall;
  }
  return new Date(guess).toISOString().replace(".000Z", "Z");
}

/** A `datetime-local` value showing an ISO instant in a display zone. */
export function isoToZonedInput(iso: string, tz: TimeZoneId): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const f = fieldsIn(d, tz);
  return `${f.year}-${two(f.month + 1)}-${two(f.day)}T${two(f.hour)}:${two(f.minute)}`;
}
