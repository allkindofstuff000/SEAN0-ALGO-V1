import { motion } from "framer-motion";
import { Bar, Doughnut, Line } from "react-chartjs-2";

import { baseChartOptions, chartPalette, minimalAxisOptions } from "../services/charts";
import { formatCurrency, formatNumber, formatPercent, formatTimestamp } from "../services/formatters";
import { PanelShell, ValueBadge } from "./ui/PanelShell";

export default function BacktestPanel({ backtest }) {
  if (!backtest?.available) {
    return (
      <PanelShell title="Backtesting Panel" subtitle="Historical validation" accent="orange">
        <div className="rounded-[22px] border border-dashed border-white/10 bg-black/20 px-5 py-10 text-sm text-zinc-400">
          No backtest artifact is available yet. Run `python run_backtest.py` to populate this panel.
        </div>
      </PanelShell>
    );
  }

  const summary = backtest.summary || {};
  const equityCurve = backtest.equityCurve || [];
  const scoreDistribution = backtest.scoreDistribution || [];
  const sessionPerformance = backtest.sessionPerformance || [];

  const equityData = {
    labels: equityCurve.map((point) => formatTimestamp(point.timestamp, { showSeconds: false })),
    datasets: [
      {
        label: "Equity Curve",
        data: equityCurve.map((point) => point.equity),
        borderColor: "#ff6a4d",
        backgroundColor: "rgba(255, 106, 77, 0.18)",
        fill: true,
        tension: 0.35,
      },
    ],
  };

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
        data: sessionPerformance.map((item) => item.winRate),
        backgroundColor: chartPalette.slice(0, sessionPerformance.length || 4),
        borderColor: "rgba(10, 0, 0, 0.75)",
        borderWidth: 2,
      },
    ],
  };

  return (
    <PanelShell
      title="Backtesting Panel"
      subtitle="Historical performance summary"
      accent="orange"
      action={<ValueBadge label="Generated" value={formatTimestamp(backtest.generatedAt, { showSeconds: false })} tone="warning" />}
    >
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {[
          ["Total Trades", formatNumber(summary.total_trades ?? 0), "Closed trades in latest backtest"],
          ["Win Rate", formatPercent(summary.win_rate ?? 0, 1), "Historical signal conversion"],
          ["Profit Factor", formatNumber(summary.profit_factor ?? 0, 2), "Gross profit over gross loss"],
          ["Net Profit", formatCurrency(summary.net_profit ?? 0, 0), "Ending result vs starting capital"],
        ].map(([label, value, hint]) => (
          <motion.div key={label} whileHover={{ y: -3 }} className="rounded-[24px] border border-white/8 bg-black/20 p-4">
            <div className="text-[0.68rem] uppercase tracking-[0.24em] text-zinc-500">{label}</div>
            <div className="mt-2 text-2xl font-bold text-white">{value}</div>
            <div className="mt-2 text-sm text-zinc-400">{hint}</div>
          </motion.div>
        ))}
      </div>

      <div className="mt-5 grid gap-5 xl:grid-cols-[1.4fr_1fr]">
        <div className="rounded-[24px] border border-white/8 bg-black/20 p-4">
          <div className="mb-4 text-[0.68rem] uppercase tracking-[0.24em] text-zinc-500">Equity Curve</div>
          <div className="chart-shell h-[280px]">
            <Line data={equityData} options={minimalAxisOptions} />
          </div>
        </div>

        <div className="grid gap-5">
          <div className="rounded-[24px] border border-white/8 bg-black/20 p-4">
            <div className="mb-4 text-[0.68rem] uppercase tracking-[0.24em] text-zinc-500">Session Performance</div>
            <div className="chart-shell h-[240px]">
              <Doughnut data={sessionData} options={{ ...baseChartOptions, scales: undefined }} />
            </div>
          </div>
        </div>
      </div>

      <div className="mt-5 rounded-[24px] border border-white/8 bg-black/20 p-4">
        <div className="mb-4 text-[0.68rem] uppercase tracking-[0.24em] text-zinc-500">Score Distribution</div>
        <div className="chart-shell h-[220px]">
          <Bar data={scoreData} options={minimalAxisOptions} />
        </div>
      </div>
    </PanelShell>
  );
}
