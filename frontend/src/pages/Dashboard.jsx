import {
  Activity,
  AlertTriangle,
  Bot,
  Gauge,
  Radar,
  ShieldAlert,
  Target,
  TrendingUp,
} from "lucide-react";

import BotControls from "../components/BotControls";
import DecisionLogs from "../components/DecisionLogs";
import MarketPanel from "../components/MarketPanel";
import RiskPanel from "../components/RiskPanel";
import SignalTable from "../components/SignalTable";
import StatCard from "../components/StatCard";
import StrategyPanel from "../components/StrategyPanel";
import { formatPercent, toConfidenceTone } from "../services/formatters";
import { PanelShell, ValueBadge } from "../components/ui/PanelShell";

function BreakdownBar({ label, value }) {
  const width = `${Math.max(8, Math.min(100, Number(value || 0)))}%`;
  return (
    <div>
      <div className="mb-2 flex items-center justify-between text-xs uppercase tracking-[0.22em] text-zinc-500">
        <span>{label}</span>
        <span className="text-zinc-300">{value}</span>
      </div>
      <div className="h-2 rounded-full bg-white/[0.05]">
        <div className="h-2 rounded-full bg-gradient-to-r from-accent-red via-accent-crimson to-accent-orange shadow-ember" style={{ width }} />
      </div>
    </div>
  );
}

export default function DashboardPage({
  snapshot,
  activeSignals,
  pendingControl,
  pendingLearning,
  savingRisk,
  onControl,
  onLearningAction,
  onRiskSave,
}) {
  const status = snapshot.status;
  const market = snapshot.marketState;
  const signal = snapshot.signal;
  const performance = snapshot.performance;
  const learning = snapshot.learning;
  const risk = snapshot.risk;
  const signalRows = snapshot.signals?.signals || [];
  const decisionLogs = snapshot.decisionLogs?.logs || [];
  const breakdown = signal?.breakdown || {};

  const cards = [
    { icon: Activity, label: "Total Signals Today", value: performance?.signalsToday ?? 0, subtext: "Live routed signals tracked by the engine", tone: "red" },
    { icon: Target, label: "Active Signals", value: activeSignals, subtext: "Recent accepted evaluations visible in the signal feed", tone: "orange" },
    { icon: TrendingUp, label: "Win Rate", value: formatPercent(performance?.winRate ?? 0, 1), subtext: "Settled trade conversion", tone: "emerald" },
    { icon: Bot, label: "Bot Status", value: status?.botStatus || "UNKNOWN", subtext: "Current runtime state", tone: "red" },
    { icon: Radar, label: "Market Regime", value: market?.regime || "UNKNOWN", subtext: "Detected by regime classification", tone: "orange" },
    { icon: Gauge, label: "Current Session", value: market?.session || "UNKNOWN", subtext: "Live session engine output", tone: "neutral" },
    { icon: AlertTriangle, label: "Loss Streak", value: performance?.lossStreak ?? risk?.lossStreak ?? 0, subtext: "Risk engine protection trigger", tone: "red" },
    { icon: ShieldAlert, label: "Strategy Threshold", value: learning?.currentThreshold ?? signal?.threshold ?? 70, subtext: "Adaptive score floor", tone: "orange" },
  ];

  return (
    <div className="space-y-5">
      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-8">
        {cards.map((card) => (
          <StatCard key={card.label} {...card} />
        ))}
      </section>

      <section className="grid gap-5 xl:grid-cols-12">
        <div className="xl:col-span-4">
          <BotControls status={status} activeSignals={activeSignals} pendingAction={pendingControl} onAction={onControl} />
        </div>
        <div className="xl:col-span-4">
          <MarketPanel market={market} />
        </div>
        <div className="xl:col-span-4">
          <PanelShell
            title="Signal Engine Panel"
            subtitle="Live scoring engine"
            accent="red"
            action={<ValueBadge label="Confidence" value={signal?.confidence || "LOW"} tone={toConfidenceTone(signal?.confidence)} />}
          >
            <div className="grid gap-4 md:grid-cols-2">
              <div className="rounded-[24px] border border-white/8 bg-black/20 p-4">
                <div className="text-[0.68rem] uppercase tracking-[0.24em] text-zinc-500">Current Signal Score</div>
                <div className="mt-2 text-3xl font-bold text-white">{signal?.score ?? 0}</div>
              </div>
              <div className="rounded-[24px] border border-white/8 bg-black/20 p-4">
                <div className="text-[0.68rem] uppercase tracking-[0.24em] text-zinc-500">Current Threshold</div>
                <div className="mt-2 text-3xl font-bold text-white">{signal?.threshold ?? 70}</div>
              </div>
              <div className="rounded-[24px] border border-white/8 bg-black/20 p-4">
                <div className="text-[0.68rem] uppercase tracking-[0.24em] text-zinc-500">Signal Confidence</div>
                <div className="mt-2 text-xl font-bold text-white">{signal?.confidence || "LOW"}</div>
              </div>
              <div className="rounded-[24px] border border-white/8 bg-black/20 p-4">
                <div className="text-[0.68rem] uppercase tracking-[0.24em] text-zinc-500">Last Signal Direction</div>
                <div className="mt-2 text-xl font-bold text-white">{signal?.direction || "NONE"}</div>
              </div>
            </div>
            <div className="mt-5 space-y-3">
              {Object.entries(breakdown).length ? (
                Object.entries(breakdown).map(([label, value]) => <BreakdownBar key={label} label={label.replace(/_/g, " ")} value={value} />)
              ) : (
                <div className="rounded-[22px] border border-dashed border-white/10 bg-black/20 px-4 py-8 text-sm text-zinc-400">
                  Score breakdown will appear when the live engine publishes a complete evaluation payload.
                </div>
              )}
            </div>
          </PanelShell>
        </div>

        <div className="xl:col-span-6">
          <StrategyPanel learning={learning} pendingAction={pendingLearning} onAction={onLearningAction} />
        </div>
        <div className="xl:col-span-6">
          <RiskPanel risk={risk} saving={savingRisk} onSave={onRiskSave} />
        </div>

        <div className="xl:col-span-7">
          <SignalTable rows={signalRows} limit={8} title="Latest Signals Table" subtitle="Recent engine evaluations" />
        </div>
        <div className="xl:col-span-5">
          <DecisionLogs logs={decisionLogs.slice(0, 8)} title="Decision Trace Preview" />
        </div>
      </section>
    </div>
  );
}
