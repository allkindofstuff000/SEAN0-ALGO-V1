import { Bot, BrainCircuit, ShieldAlert, Target } from "lucide-react";

import BotControls from "../components/BotControls";
import RiskPanel from "../components/RiskPanel";
import StatCard from "../components/StatCard";
import StrategyPanel from "../components/StrategyPanel";
import WfoPanel from "../components/WfoPanel";
import { formatPercent } from "../services/formatters";

export default function StrategyPage({
  snapshot,
  activeSignals,
  pendingControl,
  pendingLearning,
  savingRisk,
  onControl,
  onLearningAction,
  onRiskSave,
}) {
  const learning = snapshot.learning;
  const risk = snapshot.risk;
  const status = snapshot.status;
  const wfo = snapshot.wfo;

  const cards = [
    { icon: BrainCircuit, label: "Current Threshold", value: learning?.currentThreshold ?? 70, subtext: "Adaptive threshold currently in force", tone: "red" },
    { icon: Target, label: "Trades Analyzed", value: learning?.tradesAnalyzed ?? 0, subtext: "Latest optimization sample", tone: "orange" },
    { icon: ShieldAlert, label: "Overall Win Rate", value: formatPercent(learning?.overallWinRate ?? 0, 1), subtext: "Learning system quality input", tone: "neutral" },
    { icon: Bot, label: "Active Signals", value: activeSignals, subtext: "Currently visible accepted evaluations", tone: "red" },
  ];

  return (
    <div className="space-y-5">
      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {cards.map((card) => (
          <StatCard key={card.label} {...card} />
        ))}
      </section>

      <section className="grid gap-5 xl:grid-cols-12">
        <div className="xl:col-span-4">
          <StrategyPanel learning={learning} pendingAction={pendingLearning} onAction={onLearningAction} />
        </div>
        <div className="xl:col-span-4">
          <RiskPanel risk={risk} saving={savingRisk} onSave={onRiskSave} />
        </div>
        <div className="xl:col-span-4">
          <BotControls status={status} activeSignals={activeSignals} pendingAction={pendingControl} onAction={onControl} />
        </div>
      </section>

      <WfoPanel wfo={wfo} />
    </div>
  );
}
