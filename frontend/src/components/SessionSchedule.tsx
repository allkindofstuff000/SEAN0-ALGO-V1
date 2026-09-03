import { useEffect, useState } from "react";
import { SESSIONS, currentSession, dhakaRange } from "@/lib/sessions";
import { fmtLocalTime, TZ_LABEL } from "@/lib/tz";

// Full day's trading sessions in Dhaka time, with the current one highlighted
// (live) and the bot's active (trading) windows marked green. A ticking Dhaka
// clock in the header shows the schedule is live in real time.
export function SessionSchedule() {
  const [now, setNow] = useState<Date>(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  const cur = currentSession(now);   // recomputed every second from the live clock
  const clock = fmtLocalTime(now);   // HH:MM:SS in Dhaka (UTC+6)

  return (
    <div className="rounded-lg border border-border bg-card overflow-hidden">
      <div className="px-4 py-2.5 border-b border-border bg-secondary/20 flex items-center gap-2 flex-wrap">
        <span className="text-xs font-bold uppercase tracking-wider">Trading Sessions</span>
        <span className="text-[10px] text-muted-foreground font-mono hidden sm:inline">bot trades the green windows (18:00–03:00)</span>
        <span className="ml-auto flex items-center gap-1.5 font-mono text-xs">
          <span className="relative flex h-1.5 w-1.5">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-accent opacity-75" />
            <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-accent" />
          </span>
          <span className="text-accent font-bold tabular-nums">{clock}</span>
          <span className="text-muted-foreground">{TZ_LABEL}</span>
        </span>
      </div>
      <div className="divide-y divide-border/40">
        {SESSIONS.map((s) => {
          const active = s.key === cur.key;
          return (
            <div
              key={s.key}
              className={`flex items-center gap-3 px-4 py-2 transition-colors ${active ? (s.trading ? "bg-accent/10" : "bg-secondary/30") : ""}`}
            >
              <span className="text-base w-6 text-center">{s.emoji}</span>
              <span className={`text-xs font-bold w-40 ${active ? "text-foreground" : "text-muted-foreground"}`}>{s.name}</span>
              <span className="font-mono text-[11px] text-muted-foreground w-28 tabular-nums">{dhakaRange(s)}</span>
              {s.trading ? (
                <span className="text-[10px] font-bold text-accent">● TRADING{s.note ? ` · ${s.note}` : ""}</span>
              ) : (
                <span className="text-[10px] text-muted-foreground">bot idle</span>
              )}
              {active && (
                <span className={`ml-auto text-[9px] font-bold px-2 py-0.5 rounded-full ${s.trading ? "bg-accent/20 text-accent border border-accent/30" : "bg-secondary text-muted-foreground border border-border"}`}>
                  NOW
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
