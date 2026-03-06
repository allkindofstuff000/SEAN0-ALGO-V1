import { motion } from "framer-motion";

import { formatNumber, formatPercent, formatTimestamp } from "../services/formatters";
import { PanelShell, ValueBadge } from "./ui/PanelShell";

export default function WfoPanel({ wfo }) {
  if (!wfo?.available) {
    return (
      <PanelShell title="Walk Forward Optimization" subtitle="Validation windows" accent="red">
        <div className="rounded-[22px] border border-dashed border-white/10 bg-black/20 px-5 py-10 text-sm text-zinc-400">
          No WFO output is available yet. Run `python run_wfo.py` to generate validation windows.
        </div>
      </PanelShell>
    );
  }

  const overview = wfo.overview || {};
  const windows = wfo.performancePerWindow || [];
  const stability = wfo.parameterStability || {};

  return (
    <PanelShell
      title="Walk Forward Optimization"
      subtitle="Out-of-sample validation"
      accent="red"
      action={<ValueBadge label="Windows" value={overview.windows_tested ?? 0} tone="danger" />}
    >
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {[
          ["Average Win Rate", formatPercent(overview.overall_win_rate ?? 0, 1)],
          ["Profit Factor", formatNumber(overview.average_profit_factor ?? 0, 2)],
          ["Max Drawdown", formatPercent(overview.max_drawdown ?? 0, 1)],
          ["Total Trades", formatNumber(overview.total_trades ?? 0)],
        ].map(([label, value]) => (
          <motion.div key={label} whileHover={{ y: -3 }} className="rounded-[24px] border border-white/8 bg-black/20 p-4">
            <div className="text-[0.68rem] uppercase tracking-[0.24em] text-zinc-500">{label}</div>
            <div className="mt-2 text-2xl font-bold text-white">{value}</div>
          </motion.div>
        ))}
      </div>

      <div className="mt-5 rounded-[24px] border border-white/8 bg-black/20 p-4">
        <div className="mb-4 flex flex-wrap items-center gap-2 text-[0.68rem] uppercase tracking-[0.24em] text-zinc-500">
          <span>Parameter Stability</span>
          <span className="text-zinc-700">·</span>
          <span>{formatTimestamp(wfo.generatedAt, { showSeconds: false })}</span>
        </div>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          {Object.entries(stability).map(([key, value]) => (
            <div key={key} className="rounded-[20px] border border-white/8 bg-white/[0.02] p-4">
              <div className="text-[0.68rem] uppercase tracking-[0.24em] text-zinc-500">{key.replace(/_/g, " ")}</div>
              <div className="mt-2 text-xl font-bold text-white">{formatNumber(value?.mean ?? 0, 2)}</div>
              <div className="mt-1 text-sm text-zinc-400">Std {formatNumber(value?.std ?? 0, 2)}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="mt-5 overflow-x-auto">
        <table className="megaboost-table min-w-[880px]">
          <thead>
            <tr>
              <th>Window</th>
              <th>Training Win Rate</th>
              <th>Testing Win Rate</th>
              <th>Profit Factor</th>
              <th>Max Drawdown</th>
            </tr>
          </thead>
          <tbody>
            {windows.map((window) => (
              <tr key={window.window_id}>
                <td className="rounded-l-2xl font-semibold text-white">{window.window_id}</td>
                <td>{formatPercent(window.training_performance?.win_rate ?? 0, 1)}</td>
                <td>{formatPercent(window.testing_performance?.win_rate ?? 0, 1)}</td>
                <td>{formatNumber(window.testing_performance?.profit_factor ?? 0, 2)}</td>
                <td className="rounded-r-2xl">{formatPercent(window.testing_performance?.max_drawdown ?? 0, 1)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </PanelShell>
  );
}
