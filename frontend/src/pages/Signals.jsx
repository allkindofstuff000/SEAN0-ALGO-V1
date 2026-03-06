import { BarChart3, Signal, TrendingUp, Waypoints } from "lucide-react";

import DecisionLogs from "../components/DecisionLogs";
import SignalTable from "../components/SignalTable";
import StatCard from "../components/StatCard";
import { toConfidenceTone } from "../services/formatters";
import { PanelShell, ValueBadge } from "../components/ui/PanelShell";

function BreakdownRow({ label, value }) {
  const width = `${Math.max(8, Math.min(100, Number(value || 0)))}%`;
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-xs uppercase tracking-[0.24em] text-zinc-500">
        <span>{label}</span>
        <span className="text-zinc-300">{value}</span>
      </div>
      <div className="h-2 rounded-full bg-white/[0.05]">
        <div className="h-2 rounded-full bg-gradient-to-r from-accent-red via-accent-crimson to-accent-orange" style={{ width }} />
      </div>
    </div>
  );
}

export default function SignalsPage({ snapshot }) {
  const signal = snapshot.signal;
  const rows = snapshot.signals?.signals || [];
  const logs = snapshot.decisionLogs?.logs || [];
  const breakdown = signal?.breakdown || {};

  const cards = [
    { icon: BarChart3, label: "Current Score", value: signal?.score ?? 0, subtext: "Weighted output from live signal scoring", tone: "red" },
    { icon: Signal, label: "Current Threshold", value: signal?.threshold ?? 70, subtext: "Dynamic threshold from learning adapter", tone: "orange" },
    { icon: TrendingUp, label: "Signal Confidence", value: signal?.confidence || "LOW", subtext: "Confidence band derived from score delta", tone: "neutral" },
    { icon: Waypoints, label: "Last Direction", value: signal?.direction || "NONE", subtext: "Most recent routing direction", tone: "red" },
  ];

  return (
    <div className="space-y-5">
      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {cards.map((card) => (
          <StatCard key={card.label} {...card} />
        ))}
      </section>

      <section className="grid gap-5 xl:grid-cols-12">
        <div className="xl:col-span-7">
          <SignalTable rows={rows} title="Signal Engine Feed" subtitle="Latest signals table" />
        </div>
        <div className="xl:col-span-5">
          <PanelShell
            title="Signal Breakdown"
            subtitle="Weighted scoring"
            accent="red"
            action={<ValueBadge label="Confidence" value={signal?.confidence || "LOW"} tone={toConfidenceTone(signal?.confidence)} />}
          >
            <div className="space-y-4">
              {Object.entries(breakdown).length ? (
                Object.entries(breakdown).map(([label, value]) => <BreakdownRow key={label} label={label.replace(/_/g, " ")} value={value} />)
              ) : (
                <div className="rounded-[22px] border border-dashed border-white/10 bg-black/20 px-4 py-10 text-sm text-zinc-400">
                  No detailed breakdown is available yet from the latest signal payload.
                </div>
              )}
            </div>
          </PanelShell>
        </div>
      </section>

      <DecisionLogs logs={logs} title="Decision Trace Panel" />
    </div>
  );
}
