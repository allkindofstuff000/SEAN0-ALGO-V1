import { Bar, Line } from "react-chartjs-2";
import { Activity, CandlestickChart, Target, TrendingUp } from "lucide-react";

import BacktestPanel from "../components/BacktestPanel";
import StatCard from "../components/StatCard";
import WfoPanel from "../components/WfoPanel";
import { baseChartOptions, chartPalette, minimalAxisOptions } from "../services/charts";
import { formatCurrency, formatPercent } from "../services/formatters";
import { PanelShell } from "../components/ui/PanelShell";

export default function PerformancePage({ snapshot }) {
  const performance = snapshot.performance;
  const backtest = snapshot.backtest;
  const wfo = snapshot.wfo;

  const scoreDistribution = performance?.signalScoreDistribution || [];
  const sessionPerformance = performance?.sessionPerformance || [];
  const thresholdEvolution = performance?.thresholdEvolution || [];
  const signalsOverTime = performance?.signalsOverTime || [];

  const scoreData = {
    labels: scoreDistribution.map((item) => item.range),
    datasets: [
      {
        label: "Signals",
        data: scoreDistribution.map((item) => item.count),
        backgroundColor: chartPalette,
        borderRadius: 12,
      },
    ],
  };

  const sessionData = {
    labels: sessionPerformance.map((item) => item.session),
    datasets: [
      {
        label: "Win Rate",
        data: sessionPerformance.map((item) => item.winRate),
        backgroundColor: chartPalette.slice(0, sessionPerformance.length || 4),
        borderRadius: 12,
      },
    ],
  };

  const thresholdData = {
    labels: thresholdEvolution.map((item) => item.timestamp),
    datasets: [
      {
        label: "Threshold",
        data: thresholdEvolution.map((item) => item.threshold),
        borderColor: "#ff6a4d",
        backgroundColor: "rgba(255, 106, 77, 0.18)",
        fill: true,
        tension: 0.35,
      },
    ],
  };

  const signalsData = {
    labels: signalsOverTime.map((item) => item.timestamp),
    datasets: [
      {
        label: "Signals Over Time",
        data: signalsOverTime.map((item) => item.count),
        borderColor: "#ff9a3d",
        backgroundColor: "rgba(255, 154, 61, 0.18)",
        fill: true,
        tension: 0.35,
      },
    ],
  };

  const cards = [
    { icon: Activity, label: "Signals Today", value: performance?.signalsToday ?? 0, subtext: "Live runtime signal count", tone: "red" },
    { icon: TrendingUp, label: "Best Session", value: performance?.bestSession || "UNKNOWN", subtext: "Highest live settled session win rate", tone: "orange" },
    { icon: Target, label: "Worst Session", value: performance?.worstSession || "UNKNOWN", subtext: "Lowest live settled session win rate", tone: "neutral" },
    { icon: CandlestickChart, label: "Backtest Net Profit", value: formatCurrency(backtest?.summary?.net_profit ?? 0, 0), subtext: "Latest full validation run", tone: "red" },
  ];

  return (
    <div className="space-y-5">
      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {cards.map((card) => (
          <StatCard key={card.label} {...card} />
        ))}
      </section>

      <BacktestPanel backtest={backtest} />

      <section className="grid gap-5 xl:grid-cols-12">
        <div className="xl:col-span-6">
          <PanelShell title="Signal Score Distribution" subtitle="Performance analytics" accent="red">
            <div className="chart-shell h-[260px]">
              <Bar data={scoreData} options={minimalAxisOptions} />
            </div>
          </PanelShell>
        </div>
        <div className="xl:col-span-6">
          <PanelShell title="Win Rate by Session" subtitle="Performance analytics" accent="orange">
            <div className="chart-shell h-[260px]">
              <Bar data={sessionData} options={minimalAxisOptions} />
            </div>
          </PanelShell>
        </div>
        <div className="xl:col-span-6">
          <PanelShell title="Threshold Evolution" subtitle="Adaptive learning history" accent="red">
            <div className="chart-shell h-[260px]">
              <Line data={thresholdData} options={minimalAxisOptions} />
            </div>
          </PanelShell>
        </div>
        <div className="xl:col-span-6">
          <PanelShell title="Signals Over Time" subtitle="Strategy performance" accent="orange">
            <div className="chart-shell h-[260px]">
              <Line data={signalsData} options={{ ...baseChartOptions, plugins: { ...baseChartOptions.plugins, legend: { display: false } } }} />
            </div>
          </PanelShell>
        </div>
      </section>

      <WfoPanel wfo={wfo} />
    </div>
  );
}
