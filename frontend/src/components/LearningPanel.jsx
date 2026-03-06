import { formatPercent, formatTimestamp } from "../services/formatters";
import { PanelShell, DataRow, ValueBadge } from "./ui/PanelShell";

const controls = [
  { action: "enable", label: "Enable self-learning", tone: "emerald" },
  { action: "disable", label: "Disable self-learning", tone: "amber" },
  { action: "reset", label: "Reset learning system", tone: "rose" },
];

const BUTTON_STYLES = {
  emerald: "border-emerald-400/25 bg-emerald-400/12 text-emerald-100 hover:bg-emerald-400/18",
  amber: "border-amber-400/25 bg-amber-400/12 text-amber-100 hover:bg-amber-400/18",
  rose: "border-rose-400/25 bg-rose-400/12 text-rose-100 hover:bg-rose-400/18",
};

export default function LearningPanel({ learning, pendingAction, onAction }) {
  return (
    <PanelShell
      title="Strategy Learning"
      subtitle="Adaptive Threshold"
      accent="violet"
      action={<ValueBadge label="Learning" value={learning?.enabled ? "ACTIVE" : "PAUSED"} tone={learning?.enabled ? "violet" : "amber"} />}
      className="h-full"
    >
      <div className="grid gap-4 lg:grid-cols-[1.05fr_0.95fr]">
        <div className="rounded-[24px] border border-violet-400/18 bg-violet-400/8 p-5">
          <div className="grid gap-3 sm:grid-cols-2">
            <ValueBadge label="Threshold" value={learning?.currentThreshold ?? 70} tone="violet" />
            <ValueBadge label="Trades analyzed" value={learning?.tradesAnalyzed ?? 0} tone="cyan" />
            <ValueBadge label="Best score range" value={learning?.bestScoreRange || "Waiting"} tone="emerald" />
            <ValueBadge label="Win rate" value={formatPercent(learning?.overallWinRate ?? 0)} tone="amber" />
          </div>
          <div className="mt-5 rounded-2xl border border-white/8 bg-ink-950/45 p-4">
            <div className="text-xs uppercase tracking-[0.22em] text-slate-400">Last optimization</div>
            <div className="mt-2 font-display text-lg text-white">{formatTimestamp(learning?.lastOptimizationTime)}</div>
            <p className="mt-2 text-sm text-slate-300">{learning?.lastReason || "Threshold stays adaptive and updates every 100 completed trades."}</p>
          </div>
        </div>
        <div className="rounded-[24px] border border-white/8 bg-ink-900/55 p-5">
          <DataRow label="Current threshold" value={learning?.currentThreshold ?? 70} hint="Live score gate used by the signal engine" />
          <DataRow label="Optimization window" value="100 trades" hint="Threshold recalibration frequency" />
          <DataRow label="Completed trades" value={learning?.totalCompletedTrades ?? 0} hint="Historical dataset available to the optimizer" />
          <div className="mt-5 grid gap-3">
            {controls.map((control) => (
              <button
                key={control.action}
                type="button"
                onClick={() => onAction(control.action)}
                disabled={Boolean(pendingAction)}
                className={`rounded-2xl border px-4 py-3 text-left transition disabled:cursor-not-allowed disabled:opacity-60 ${BUTTON_STYLES[control.tone]}`}
              >
                <div className="font-display text-sm font-semibold">{control.label}</div>
                <div className="mt-1 text-xs uppercase tracking-[0.22em] text-slate-300/80">
                  {pendingAction === control.action ? "Processing" : "Update learning state"}
                </div>
              </button>
            ))}
          </div>
        </div>
      </div>
    </PanelShell>
  );
}
