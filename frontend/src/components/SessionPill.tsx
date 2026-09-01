import { useEffect, useState } from "react";
import { fmtLocalTime, TZ_LABEL } from "@/lib/tz";
import { currentSession } from "@/lib/sessions";

export function SessionPill() {
  // Re-render every 1s for the clock; the pill also flips at session boundaries.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1_000);
    return () => clearInterval(id);
  }, []);

  const s = currentSession();
  return (
    <div className="inline-flex items-center gap-2 flex-wrap">
      <div
        className={`inline-flex items-center gap-2 px-3 py-1 rounded-full border text-xs font-bold ${
          s.trading
            ? "bg-accent/10 border-accent/20 text-accent"
            : "bg-secondary/60 border-border text-muted-foreground"
        }`}
        title="Sessions run in UTC · strategies trade 12:00–21:00 UTC (18:00–03:00 UTC+6)"
      >
        <span
          className={`w-1.5 h-1.5 rounded-full ${
            s.trading ? "bg-accent animate-pulse" : "bg-yellow-500/80"
          }`}
        />
        SESSION: {s.name}
        <span className={s.trading ? "text-accent/70" : "text-muted-foreground/70"}>
          · {s.trading ? "trading window" : "bot idle"}
        </span>
      </div>
      <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full border border-border bg-background/60 text-xs font-mono text-muted-foreground">
        {fmtLocalTime(now)} <span className="text-[10px] opacity-70">{TZ_LABEL}</span>
      </div>
    </div>
  );
}
