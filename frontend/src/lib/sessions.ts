// Trading-session model. Times are anchored in UTC (the engine's clock) and
// displayed in Dhaka (UTC+6). The bot trades ONLY the sessions flagged
// trading:true — i.e. 12:00–21:00 UTC (London–NY Overlap + New York), which is
// where gold is most liquid. This is display-only; it does not change what the
// engine trades (that filter lives in the backend, session_allowed 12–21 UTC).

export type Sess = {
  key: string;
  name: string;
  emoji: string;
  utcStart: number; // inclusive UTC hour
  utcEnd: number;   // exclusive UTC hour
  trading: boolean;
  note?: string;
};

export const SESSIONS: Sess[] = [
  { key: "asian",    name: "Asian",             emoji: "🌏", utcStart: 0,  utcEnd: 7,  trading: false },
  { key: "london",   name: "London",            emoji: "🏙️", utcStart: 7,  utcEnd: 12, trading: false },
  { key: "overlap",  name: "London–NY Overlap", emoji: "🔥", utcStart: 12, utcEnd: 16, trading: true, note: "peak — most signals" },
  { key: "newyork",  name: "New York",          emoji: "🗽", utcStart: 16, utcEnd: 21, trading: true },
  { key: "offhours", name: "Off-hours",         emoji: "🌙", utcStart: 21, utcEnd: 24, trading: false },
];

export const utcToDhaka = (h: number): number => (h + 6) % 24;

export function currentSession(now: Date = new Date()): Sess {
  const h = now.getUTCHours();
  return SESSIONS.find((s) => h >= s.utcStart && h < s.utcEnd) ?? SESSIONS[0];
}

const pad = (n: number) => String(n).padStart(2, "0");

/** Dhaka-time range label, e.g. "18:00–22:00". */
export function dhakaRange(s: Sess): string {
  return `${pad(utcToDhaka(s.utcStart))}:00–${pad(utcToDhaka(s.utcEnd))}:00`;
}
