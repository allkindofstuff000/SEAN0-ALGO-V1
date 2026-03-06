import { formatPercent } from "../services/formatters";
import { PanelShell, ValueBadge } from "./ui/PanelShell";

export default function PerformancePanel({ performance }) {
  const sessionPerformance = [...(performance?.sessionPerformance || [])].sort((left, right) => right.winRate - left.winRate);

  return (
    <PanelShell
      title="Performance Analytics"
      subtitle="Execution Quality"
      accent="emerald"
      action={<ValueBadge label="Signals today" value={performance?.signalsToday ?? 0} tone="cyan" />}
      className="h-full"
    >
      <div className="grid gap-4 lg:grid-cols-[1fr_1fr]">
        <div className="grid gap-3 sm:grid-cols-2">
          <ValueBadge label="Win rate" value={formatPercent(performance?.winRate ?? 0)} tone="emerald" />
          <ValueBadge label="Loss streak" value={performance?.lossStreak ?? 0} tone={performance?.lossStreak > 0 ? "rose" : "emerald"} />
          <ValueBadge label="Best session" value={performance?.bestSession || "--"} tone="violet" />
          <ValueBadge label="Worst session" value={performance?.worstSession || "--"} tone="amber" />
        </div>
        <div className="rounded-[24px] border border-white/8 bg-ink-900/55 p-5">
          <div className="text-xs uppercase tracking-[0.24em] text-slate-400">Session leaderboard</div>
          <div className="mt-4 space-y-3">
            {sessionPerformance.length ? (
              sessionPerformance.map((row) => (
                <div key={row.session}>
                  <div className="flex items-center justify-between gap-3 text-sm text-slate-300">
                    <span className="font-display text-white">{row.session}</span>
                    <span>{formatPercent(row.winRate, 2)}</span>
                  </div>
                  <div className="mt-2 h-2 overflow-hidden rounded-full bg-white/5">
                    <div className="h-full rounded-full bg-gradient-to-r from-emerald-300 via-cyan-300 to-signal-blue" style={{ width: `${Math.max(6, Math.min(100, row.winRate))}%` }} />
                  </div>
                </div>
              ))
            ) : (
              <div className="rounded-2xl border border-dashed border-white/10 px-4 py-8 text-sm text-slate-400">Session performance appears here after the trade log begins to accumulate settled outcomes.</div>
            )}
          </div>
        </div>
      </div>
    </PanelShell>
  );
}
