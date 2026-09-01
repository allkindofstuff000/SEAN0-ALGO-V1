// Display timezone for the whole dashboard.
// IMPORTANT: this is presentation-only. The backend, the strategies, the OANDA
// feed and the session filter all run in UTC and must NOT be changed — we only
// convert timestamps to this zone when showing them to the user.
//
// Bangladesh (Asia/Dhaka) is a fixed UTC+6 with no daylight saving.
export const DISPLAY_TZ = "Asia/Dhaka";
export const TZ_LABEL = "UTC+6";

/** Parse any backend timestamp into a Date, treating tz-less strings as UTC.
 *  - ISO with offset ("2026-08-31T12:35:16+00:00") → parsed as-is
 *  - tz-less ("2026-08-28 12:40:00", from backtest records) → assumed UTC
 *  - epoch seconds or ms (number) → converted
 */
export function toUtcDate(input: string | number | Date | null | undefined): Date | null {
  if (input == null || input === "") return null;
  if (input instanceof Date) return isNaN(input.getTime()) ? null : input;
  if (typeof input === "number") {
    const d = new Date(input < 1e12 ? input * 1000 : input); // sec vs ms
    return isNaN(d.getTime()) ? null : d;
  }
  let s = input.trim().replace(" ", "T");
  // Append Z when the string carries no timezone marker → treat as UTC.
  if (!/[zZ]|[+-]\d{2}:?\d{2}$/.test(s)) s += "Z";
  const d = new Date(s);
  return isNaN(d.getTime()) ? null : d;
}

const _dtCache = new Map<string, Intl.DateTimeFormat>();
function _fmt(key: string, opts: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  let f = _dtCache.get(key);
  if (!f) {
    f = new Intl.DateTimeFormat("en-GB", { timeZone: DISPLAY_TZ, hour12: false, ...opts });
    _dtCache.set(key, f);
  }
  return f;
}

/** "2026-08-31 18:35" in Dhaka time (date + HH:MM). */
export function fmtLocal(input: string | number | Date | null | undefined): string {
  const d = toUtcDate(input);
  if (!d) return "—";
  const p = _fmt("dt", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit",
  }).formatToParts(d).reduce((a, x) => ((a[x.type] = x.value), a), {} as Record<string, string>);
  return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}`;
}

/** "18:35" in Dhaka time (time only). */
export function fmtLocalTime(input: string | number | Date | null | undefined): string {
  const d = toUtcDate(input);
  if (!d) return "—";
  const p = _fmt("t", { hour: "2-digit", minute: "2-digit", second: "2-digit" })
    .formatToParts(d).reduce((a, x) => ((a[x.type] = x.value), a), {} as Record<string, string>);
  return `${p.hour}:${p.minute}:${p.second}`;
}

/** "2026-08-31" in Dhaka time (date only). */
export function fmtLocalDate(input: string | number | Date | null | undefined): string {
  const d = toUtcDate(input);
  if (!d) return "—";
  const p = _fmt("d", { year: "numeric", month: "2-digit", day: "2-digit" })
    .formatToParts(d).reduce((a, x) => ((a[x.type] = x.value), a), {} as Record<string, string>);
  return `${p.year}-${p.month}-${p.day}`;
}
