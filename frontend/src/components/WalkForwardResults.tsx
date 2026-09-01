import type { RsiBacktestResult, RsiMetrics } from "@/lib/api";
import { walkForwardVerdict, type WfTone } from "@/lib/walkforward";

const toneClasses: Record<WfTone, string> = {
  good: "text-accent border-accent/40 bg-accent/10",
  bad: "text-destructive border-destructive/40 bg-destructive/10",
  warn: "text-yellow-500 border-yellow-500/40 bg-yellow-500/10",
  neutral: "text-muted-foreground border-border bg-secondary/20",
};

function pct(m: RsiMetrics, startBal: number): number {
  return (((m.ending_balance ?? startBal) - startBal) / startBal) * 100;
}

export function WalkForwardResults({
  train,
  test,
  startBalance,
  splitDate,
}: {
  train: RsiBacktestResult;
  test: RsiBacktestResult;
  startBalance: number;
  splitDate: string;
}) {
  const tm = train.metrics;
  const sm = test.metrics;
  const v = walkForwardVerdict(tm, sm, startBalance);

  const rows: { label: string; t: string; s: string; good?: (m: RsiMetrics) => boolean }[] = [
    { label: "Return %", t: `${pct(tm, startBalance) >= 0 ? "+" : ""}${pct(tm, startBalance).toFixed(1)}%`, s: `${pct(sm, startBalance) >= 0 ? "+" : ""}${pct(sm, startBalance).toFixed(1)}%` },
    { label: "Win rate", t: `${(tm.win_rate ?? 0).toFixed(1)}%`, s: `${(sm.win_rate ?? 0).toFixed(1)}%` },
    { label: "Profit factor", t: Number.isFinite(tm.profit_factor) ? tm.profit_factor.toFixed(2) : "∞", s: Number.isFinite(sm.profit_factor) ? sm.profit_factor.toFixed(2) : "∞" },
    { label: "Trades", t: `${tm.total_trades ?? 0}`, s: `${sm.total_trades ?? 0}` },
    { label: "Max DD (R)", t: `${(tm.max_drawdown_r ?? 0).toFixed(2)}`, s: `${(sm.max_drawdown_r ?? 0).toFixed(2)}` },
  ];

  return (
    <div className="rounded-lg border border-border bg-card overflow-hidden">
      <div className="px-4 py-3 border-b border-border bg-secondary/20 flex items-center gap-2 flex-wrap">
        <p className="text-xs font-bold uppercase tracking-wider">Walk-Forward Validation</p>
        <p className="text-[10px] text-muted-foreground font-mono">train ends {splitDate} · test is unseen data after it</p>
      </div>

      {/* Verdict banner */}
      <div className={`mx-4 mt-3 rounded-md border px-4 py-2.5 flex items-start gap-3 ${toneClasses[v.tone]}`}>
        <span className="font-mono font-bold text-sm mt-0.5">{v.label}</span>
        <span className="text-xs leading-relaxed opacity-90">{v.detail}</span>
      </div>

      {/* Side-by-side table */}
      <div className="p-4">
        <table className="w-full">
          <thead>
            <tr className="border-b border-border">
              <th className="text-left py-2 text-[10px] uppercase font-bold text-muted-foreground">Metric</th>
              <th className="text-right py-2 text-[10px] uppercase font-bold text-muted-foreground">Train (in-sample)</th>
              <th className="text-right py-2 text-[10px] uppercase font-bold text-primary">Test (out-of-sample)</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.label} className="border-b border-border/40">
                <td className="py-2 text-[11px] text-muted-foreground">{r.label}</td>
                <td className="py-2 text-right font-mono text-xs">{r.t}</td>
                <td className="py-2 text-right font-mono text-xs font-bold">{r.s}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="text-[10px] text-muted-foreground mt-3 leading-relaxed">
          The <span className="text-primary font-bold">Test</span> column is data the parameters never saw. If it stays profitable there,
          the edge is real — not curve-fit. If it collapses, the strategy was overfit to the past.
        </p>
      </div>
    </div>
  );
}
