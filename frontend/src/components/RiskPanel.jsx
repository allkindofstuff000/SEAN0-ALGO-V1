import { useEffect, useState } from "react";

import { formatNumber } from "../services/formatters";
import { DataRow, PanelShell, ValueBadge } from "./ui/PanelShell";

export default function RiskPanel({ risk, saving, onSave }) {
  const [formState, setFormState] = useState({
    maxSignalsPerDay: 3,
    maxLossStreak: 2,
    cooldownCandles: 3,
    riskPercentage: 1,
    signalScoreThreshold: 70,
  });

  useEffect(() => {
    setFormState({
      maxSignalsPerDay: risk?.maxSignalsPerDay ?? 3,
      maxLossStreak: risk?.maxLossStreak ?? 2,
      cooldownCandles: risk?.cooldownCandles ?? 3,
      riskPercentage: risk?.riskPercentage ?? 1,
      signalScoreThreshold: risk?.signalScoreThreshold ?? 70,
    });
  }, [risk]);

  const updateField = (field, value) => {
    setFormState((current) => ({
      ...current,
      [field]: value,
    }));
  };

  const handleSubmit = (event) => {
    event.preventDefault();
    onSave({
      maxSignalsPerDay: Number(formState.maxSignalsPerDay),
      maxLossStreak: Number(formState.maxLossStreak),
      cooldownCandles: Number(formState.cooldownCandles),
      riskPercentage: Number(formState.riskPercentage),
      signalScoreThreshold: Number(formState.signalScoreThreshold),
    });
  };

  return (
    <PanelShell title="Risk Manager Panel" subtitle="Protection controls" accent="orange" action={<ValueBadge label="Loss Streak" value={risk?.lossStreak ?? 0} tone="danger" />}>
      <form className="space-y-4" onSubmit={handleSubmit}>
        <div className="grid gap-4 md:grid-cols-2">
          <label className="space-y-2 text-sm text-zinc-300">
            <span className="text-[0.68rem] uppercase tracking-[0.24em] text-zinc-500">Max Signals Per Day</span>
            <input className="megaboost-input" type="number" min="1" value={formState.maxSignalsPerDay} onChange={(event) => updateField("maxSignalsPerDay", event.target.value)} />
          </label>
          <label className="space-y-2 text-sm text-zinc-300">
            <span className="text-[0.68rem] uppercase tracking-[0.24em] text-zinc-500">Cooldown Candles</span>
            <input className="megaboost-input" type="number" min="1" value={formState.cooldownCandles} onChange={(event) => updateField("cooldownCandles", event.target.value)} />
          </label>
          <label className="space-y-2 text-sm text-zinc-300">
            <span className="text-[0.68rem] uppercase tracking-[0.24em] text-zinc-500">Max Loss Streak</span>
            <input className="megaboost-input" type="number" min="1" value={formState.maxLossStreak} onChange={(event) => updateField("maxLossStreak", event.target.value)} />
          </label>
          <label className="space-y-2 text-sm text-zinc-300">
            <span className="text-[0.68rem] uppercase tracking-[0.24em] text-zinc-500">Risk Percentage</span>
            <input className="megaboost-input" type="number" min="0.1" step="0.1" value={formState.riskPercentage} onChange={(event) => updateField("riskPercentage", event.target.value)} />
          </label>
          <label className="space-y-2 text-sm text-zinc-300 md:col-span-2">
            <span className="text-[0.68rem] uppercase tracking-[0.24em] text-zinc-500">Signal Score Threshold</span>
            <input className="megaboost-input" type="number" min="60" max="85" value={formState.signalScoreThreshold} onChange={(event) => updateField("signalScoreThreshold", event.target.value)} />
          </label>
        </div>

        <div className="grid gap-3 rounded-[22px] border border-white/8 bg-black/20 p-4 md:grid-cols-2">
          <DataRow label="Current Loss Streak" value={risk?.lossStreak ?? 0} hint="Live risk state from the trading engine" />
          <DataRow label="Configured Threshold" value={formatNumber(formState.signalScoreThreshold)} hint="Updates adaptive threshold state when saved" />
        </div>

        <button className="megaboost-button w-full" type="submit" disabled={saving}>
          {saving ? "Updating Config..." : "Update Config"}
        </button>
      </form>
    </PanelShell>
  );
}
