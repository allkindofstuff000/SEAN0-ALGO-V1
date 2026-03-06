import { motion } from "framer-motion";

import { formatTimestamp } from "../services/formatters";
import { PanelShell } from "./ui/PanelShell";

const badgeTone = {
  HIGH: "success",
  MEDIUM: "warning",
  LOW: "danger",
  OPEN: "success",
  REJECTED: "danger",
  ROUTED: "warning",
};

function Badge({ value }) {
  const tone = badgeTone[String(value).toUpperCase()] || "neutral";
  const classes = {
    success: "border-emerald-400/22 bg-emerald-500/10 text-emerald-100",
    warning: "border-accent-orange/25 bg-accent-orange/10 text-orange-100",
    danger: "border-accent-red/25 bg-accent-red/10 text-red-100",
    neutral: "border-white/10 bg-white/[0.03] text-zinc-200",
  };

  return <span className={`inline-flex rounded-full border px-3 py-1 text-[0.68rem] font-semibold uppercase tracking-[0.24em] ${classes[tone]}`}>{value}</span>;
}

export default function SignalTable({ rows, limit, title = "Signal Engine", subtitle = "Latest evaluations" }) {
  const entries = typeof limit === "number" ? (rows || []).slice(0, limit) : rows || [];

  return (
    <PanelShell title={title} subtitle={subtitle} accent="red">
      {entries.length ? (
        <div className="overflow-x-auto">
          <table className="megaboost-table min-w-[880px]">
            <thead>
              <tr>
                <th>Timestamp</th>
                <th>Pair</th>
                <th>Direction</th>
                <th>Score</th>
                <th>Confidence</th>
                <th>Market Regime</th>
                <th>Session</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((row, index) => (
                <motion.tr
                  key={`${row.timestamp || index}-${row.direction || "signal"}`}
                  initial={{ opacity: 0, y: 4 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: index * 0.02 }}
                >
                  <td className="rounded-l-2xl text-zinc-300">{formatTimestamp(row.timestamp, { showSeconds: false })}</td>
                  <td className="font-semibold text-white">{row.pair || "XAUUSD"}</td>
                  <td>
                    <Badge value={row.direction || "NONE"} />
                  </td>
                  <td>
                    <div className="text-lg font-bold text-white">{row.score ?? 0}</div>
                    <div className="text-xs uppercase tracking-[0.22em] text-zinc-500">Threshold {row.threshold ?? 70}</div>
                  </td>
                  <td>
                    <Badge value={row.confidence || "LOW"} />
                  </td>
                  <td>{row.marketRegime || "UNKNOWN"}</td>
                  <td>{row.session || "UNKNOWN"}</td>
                  <td className="rounded-r-2xl">
                    <Badge value={row.status || "REJECTED"} />
                  </td>
                </motion.tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="rounded-[22px] border border-dashed border-white/10 bg-black/20 px-5 py-10 text-sm text-zinc-400">
          No signal rows are available yet. The table will populate after the engine records decision traces or routed signals.
        </div>
      )}
    </PanelShell>
  );
}
