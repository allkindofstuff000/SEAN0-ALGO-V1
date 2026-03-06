import { formatTimestamp, numeric } from "../services/formatters";
import { PanelShell, ValueBadge } from "./ui/PanelShell";

const normalizeFlag = (value) => {
  if (typeof value === "boolean") {
    return value;
  }
  if (typeof value === "number") {
    return value > 0;
  }
  if (typeof value === "string") {
    return ["TRUE", "PASS", "YES"].includes(value.trim().toUpperCase());
  }
  return false;
};

function TraceChip({ label, passed }) {
  return (
    <div
      className={`rounded-full border px-3 py-2 text-[0.68rem] font-semibold uppercase tracking-[0.24em] ${
        passed ? "border-emerald-400/22 bg-emerald-500/10 text-emerald-100" : "border-accent-red/22 bg-accent-red/10 text-red-100"
      }`}
    >
      {label}: {passed ? "PASS" : "FAIL"}
    </div>
  );
}

export default function DecisionLogs({ logs, title = "Decision Trace Panel" }) {
  const entries = logs || [];

  return (
    <PanelShell title={title} subtitle="Signal reasoning" accent="red" action={<ValueBadge label="Entries" value={entries.length} tone="danger" />}>
      <div className="max-h-[680px] space-y-4 overflow-y-auto pr-1">
        {entries.length ? (
          entries.map((log, index) => {
            const generated = Boolean(log.signal_generated);
            const score = numeric(log.signal_score ?? log.score, 0);
            const threshold = numeric(log.score_threshold, 70);
            const reason = log.reason || (generated ? "Signal accepted" : "Signal rejected");

            return (
              <article key={`${log.timestamp || index}-${score}`} className="rounded-[24px] border border-white/8 bg-black/20 p-4">
                <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
                  <div>
                    <div className="text-[0.68rem] uppercase tracking-[0.24em] text-zinc-500">{formatTimestamp(log.timestamp, { showSeconds: false })}</div>
                    <div className="mt-2 flex flex-wrap items-center gap-3">
                      <div className="text-lg font-bold text-white">{log.direction || "NO SIGNAL"}</div>
                      <div
                        className={`rounded-full border px-3 py-1 text-[0.68rem] font-semibold uppercase tracking-[0.24em] ${
                          generated ? "border-emerald-400/22 bg-emerald-500/10 text-emerald-100" : "border-accent-red/22 bg-accent-red/10 text-red-100"
                        }`}
                      >
                        {generated ? "ACCEPTED" : "REJECTED"}
                      </div>
                    </div>
                  </div>
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                    <ValueBadge label="Score" value={score} tone={generated ? "success" : "danger"} />
                    <ValueBadge label="Threshold" value={threshold} tone="warning" />
                    <ValueBadge label="Regime" value={log.market_regime || log.regime || "UNKNOWN"} tone="neutral" />
                  </div>
                </div>

                <div className="mt-4 flex flex-wrap gap-2">
                  <TraceChip label="Trend Alignment" passed={normalizeFlag(log.trend_alignment)} />
                  <TraceChip label="Liquidity Sweep" passed={normalizeFlag(log.liquidity_sweep)} />
                  <TraceChip label="ATR Expansion" passed={normalizeFlag(log.atr_expansion)} />
                </div>

                <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                  <div className="rounded-[20px] border border-white/8 bg-white/[0.02] p-3">
                    <div className="text-[0.68rem] uppercase tracking-[0.24em] text-zinc-500">Session</div>
                    <div className="mt-2 text-sm font-semibold text-white">{log.session || "UNKNOWN"}</div>
                  </div>
                  <div className="rounded-[20px] border border-white/8 bg-white/[0.02] p-3">
                    <div className="text-[0.68rem] uppercase tracking-[0.24em] text-zinc-500">Price</div>
                    <div className="mt-2 text-sm font-semibold text-white">{log.price ?? "--"}</div>
                  </div>
                  <div className="rounded-[20px] border border-white/8 bg-white/[0.02] p-3">
                    <div className="text-[0.68rem] uppercase tracking-[0.24em] text-zinc-500">Result</div>
                    <div className="mt-2 text-sm font-semibold text-white">{generated ? "Signal Generated" : "Signal Rejected"}</div>
                  </div>
                  <div className="rounded-[20px] border border-white/8 bg-white/[0.02] p-3">
                    <div className="text-[0.68rem] uppercase tracking-[0.24em] text-zinc-500">Reason</div>
                    <div className="mt-2 text-sm font-semibold text-white">{reason}</div>
                  </div>
                </div>
              </article>
            );
          })
        ) : (
          <div className="rounded-[22px] border border-dashed border-white/10 bg-black/20 px-5 py-10 text-sm text-zinc-400">
            Decision trace records will appear here once the engine finishes a signal evaluation cycle.
          </div>
        )}
      </div>
    </PanelShell>
  );
}
