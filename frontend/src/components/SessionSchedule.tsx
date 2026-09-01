import { useEffect, useState } from "react";
import { SESSIONS, currentSession, dhakaRange } from "@/lib/sessions";

// Full day's trading sessions in Dhaka time, with the current one highlighted
// and the bot's active (trading) windows marked green.
export function SessionSchedule() {
  const [, tick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => tick((x) => x + 1), 30_000);
    return () => clearInterval(id);
  }, []);

  const cur = currentSession();

  return (
    <div className="rounded-lg border border-border bg-card overflow-hidden">
      <div className="px-4 py-2.5 border-b border-border bg-secondary/20 flex items-center gap-2 flex-wrap">
        <span className="text-xs font-bold uppercase tracking-wider">Trading Sessions</span>
        <span className="text-[10px] text-muted-foreground font-mono">Dhaka time (UTC+6) · bot trades the green windows (18:00–03:00)</span>
      </div>
      <div className="divide-y divide-border/40">
        {SESSIONS.map((s) => {
          const active = s.key === cur.key;
          return (
            <div
              key={s.key}
              className={`flex items-center gap-3 px-4 py-2 ${active ? (s.trading ? "bg-accent/10" : "bg-secondary/30") : ""}`}
            >
              <span className="text-base w-6 text-center">{s.emoji}</span>
              <span className={`text-xs font-bold w-40 ${active ? "text-foreground" : "text-muted-foreground"}`}>{s.name}</span>
              <span className="font-mono text-[11px] text-muted-foreground w-28">{dhakaRange(s)}</span>
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
