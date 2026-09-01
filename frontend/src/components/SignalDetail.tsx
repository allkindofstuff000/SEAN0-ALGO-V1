import type { LiveSignal } from "@/lib/api";
import { fmtLocal, TZ_LABEL } from "@/lib/tz";

// Small helpers ---------------------------------------------------------------
function Check({ ok, label }: { ok?: boolean | null; label: string }) {
  const known = ok === true || ok === false;
  return (
    <div className="flex items-center gap-1.5">
      <span className={ok ? "text-accent" : known ? "text-destructive" : "text-muted-foreground"}>
        {ok ? "✓" : known ? "✗" : "—"}
      </span>
      <span className="text-[11px] text-muted-foreground">{label}</span>
    </div>
  );
}

function Field({ label, value, tone }: { label: string; value: React.ReactNode; tone?: "good" | "bad" | "muted" }) {
  const c = tone === "good" ? "text-accent" : tone === "bad" ? "text-destructive" : "text-foreground";
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[9px] uppercase tracking-wider text-muted-foreground font-bold">{label}</span>
      <span className={`font-mono text-xs ${c}`}>{value}</span>
    </div>
  );
}

const num = (v: unknown, d = 2) => (typeof v === "number" && Number.isFinite(v) ? v.toFixed(d) : "—");

export function SignalDetail({ s }: { s: LiveSignal }) {
  const isBuy = (s.direction || "").toUpperCase() === "BUY";
  const entry = s.entry_price;
  const sl = s.stop_loss ?? null;
  const tp = s.take_profit ?? null;

  // distances + RR
  const slDist = sl != null && entry != null ? Math.abs(entry - sl) : null;
  const tpDist = tp != null && entry != null ? Math.abs(entry - tp) : null;
  const rr = slDist && tpDist ? tpDist / slDist : null;

  // P&L (if resolved)
  let r: number | null = null;
  let pips: number | null = null;
  if (s.outcome && s.exit_price != null && entry != null && slDist) {
    pips = isBuy ? s.exit_price - entry : entry - s.exit_price;
    r = pips / slDist;
  }

  // hold: candle time -> resolved time
  let hold = "—";
  const start = s.candle_time_utc || s.sent_at;
  const end = s.marked_at;
  if (start && end) {
    const ms = new Date(String(end).replace(" ", "T")).getTime() - new Date(String(start).replace(" ", "T")).getTime();
    if (Number.isFinite(ms) && ms >= 0) {
      const m = Math.round(ms / 60000);
      hold = m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`;
    }
  }

  const strat = s.strategyName || s.strategy || s.signal_kind || "—";

  return (
    <div className="bg-secondary/20 border-t border-border/40 px-4 py-3 grid grid-cols-1 md:grid-cols-4 gap-4">
      {/* Why it fired */}
      <div className="space-y-2">
        <p className="text-[10px] uppercase font-bold tracking-wider text-primary">Why it fired</p>
        <Check ok={s.trend_alignment} label="Trend aligned (EMA50/200)" />
        <Check ok={s.price_trigger} label="Breakout of prior candle" />
        <Check ok={s.rsi_filter} label="RSI filter (55/45)" />
        <Check ok={s.atr_expansion} label="ATR expansion (volatility)" />
        <div className="pt-1">
          <Field label="Score" value={`${s.score ?? "—"} / ${s.score_threshold ?? "—"}`} tone={(s.score ?? 0) >= (s.score_threshold ?? 999) ? "good" : "muted"} />
        </div>
      </div>

      {/* Timing */}
      <div className="space-y-2">
        <p className="text-[10px] uppercase font-bold tracking-wider text-primary">Timing ({TZ_LABEL})</p>
        <Field label="Signal candle" value={fmtLocal(s.candle_time_utc)} />
        <Field label="Alert sent" value={fmtLocal(s.sent_at)} />
        <Field label="Resolved" value={s.marked_at ? fmtLocal(s.marked_at) : (s.outcome ? "—" : "open")} />
        <Field label="Hold time" value={hold} tone="muted" />
      </div>

      {/* Trade math */}
      <div className="space-y-2">
        <p className="text-[10px] uppercase font-bold tracking-wider text-primary">Trade setup</p>
        <div className="grid grid-cols-2 gap-2">
          <Field label="Entry" value={num(entry)} />
          <Field label="RR" value={rr ? `1:${rr.toFixed(1)}` : "—"} />
          <Field label="Stop" value={sl != null ? `${num(sl)}` : "—"} tone="bad" />
          <Field label="SL dist" value={slDist ? `${slDist.toFixed(1)} pts` : "—"} tone="muted" />
          <Field label="Target" value={tp != null ? `${num(tp)}` : "—"} tone="good" />
          <Field label="TP dist" value={tpDist ? `${tpDist.toFixed(1)} pts` : "—"} tone="muted" />
          <Field label="ATR" value={num(s.atr)} />
          <Field label="Risk" value="2%" tone="muted" />
        </div>
      </div>

      {/* Market + Outcome */}
      <div className="space-y-2">
        <p className="text-[10px] uppercase font-bold tracking-wider text-primary">Context &amp; result</p>
        <Field label="Strategy" value={strat} />
        <Field label="Session" value={s.session || "—"} />
        <Field label="Market regime" value={s.market_regime ? `${s.market_regime}${s.regime_confidence != null ? ` (${(s.regime_confidence * 100).toFixed(0)}%)` : ""}` : "—"} />
        {s.outcome ? (
          <>
            <Field
              label="Outcome"
              value={`${s.outcome} @ ${num(s.exit_price)}`}
              tone={s.outcome === "WIN" ? "good" : "bad"}
            />
            <Field
              label="P&L"
              value={r != null ? `${r >= 0 ? "+" : ""}${r.toFixed(2)}R  ·  ${pips! >= 0 ? "+" : ""}${pips!.toFixed(1)} pts` : "—"}
              tone={(r ?? 0) >= 0 ? "good" : "bad"}
            />
            {s.outcome_note && <span className="text-[10px] text-muted-foreground">{s.outcome_note}</span>}
          </>
        ) : (
          <Field label="Outcome" value="open — awaiting TP/SL" tone="muted" />
        )}
      </div>
    </div>
  );
}
