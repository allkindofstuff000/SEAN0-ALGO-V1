import { motion } from "framer-motion";
import { BrainCircuit, History, SlidersHorizontal } from "lucide-react";

import { formatPercent, formatTimestamp } from "../services/formatters";
import { DataRow, PanelShell, ValueBadge } from "./ui/PanelShell";

export default function StrategyPanel({ learning, pendingAction, onAction }) {
  const controls = [
    { id: "enable", label: "Enable Learning", className: "megaboost-button", icon: BrainCircuit },
    { id: "disable", label: "Disable Learning", className: "megaboost-button-muted", icon: SlidersHorizontal },
    { id: "reset", label: "Reset Learning", className: "megaboost-button-danger", icon: History },
  ];

  return (
    <PanelShell
      title="Strategy Learning Panel"
      subtitle="Adaptive threshold engine"
      accent="red"
      action={<ValueBadge label="Learning" value={learning?.enabled ? "ENABLED" : "DISABLED"} tone={learning?.enabled ? "success" : "danger"} />}
    >
      <div className="grid gap-4 md:grid-cols-2">
        <div className="rounded-[24px] border border-white/8 bg-black/20 p-4">
          <div className="text-[0.68rem] uppercase tracking-[0.24em] text-zinc-500">Current Threshold</div>
          <div className="mt-2 text-3xl font-bold text-white">{learning?.currentThreshold ?? 70}</div>
          <div className="mt-2 text-sm text-zinc-400">Best score range {learning?.bestScoreRange || "Waiting for trade sample"}</div>
        </div>
        <div className="rounded-[24px] border border-white/8 bg-black/20 p-4">
          <div className="text-[0.68rem] uppercase tracking-[0.24em] text-zinc-500">Optimization Time</div>
          <div className="mt-2 text-lg font-semibold text-white">{formatTimestamp(learning?.lastOptimizationTime, { showSeconds: false })}</div>
          <div className="mt-2 text-sm text-zinc-400">{learning?.tradesAnalyzed ?? 0} trades analyzed in latest optimizer window</div>
        </div>
      </div>

      <div className="mt-5 grid gap-3">
        <DataRow label="Overall Win Rate" value={formatPercent(learning?.overallWinRate ?? 0, 1)} hint="Threshold adapter input signal quality" />
        <DataRow label="Last Optimization Reason" value={learning?.lastReason || "initial_threshold"} hint="Most recent threshold update trigger" />
        <DataRow label="Total Completed Trades" value={learning?.totalCompletedTrades ?? 0} hint="Full trade sample stored by the learning system" />
      </div>

      <div className="mt-5 grid gap-3 md:grid-cols-3">
        {controls.map((control) => {
          const Icon = control.icon;
          const busy = pendingAction === control.id;
          return (
            <motion.button
              key={control.id}
              type="button"
              whileHover={{ scale: 1.01 }}
              whileTap={{ scale: 0.98 }}
              className={`${control.className} ${busy ? "opacity-80" : ""}`}
              disabled={Boolean(pendingAction)}
              onClick={() => onAction(control.id)}
            >
              <Icon className="h-4 w-4" />
              {busy ? `${control.label}...` : control.label}
            </motion.button>
          );
        })}
      </div>
    </PanelShell>
  );
}
