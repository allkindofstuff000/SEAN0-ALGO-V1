import { formatTimestamp, numeric, titleize } from "../services/formatters";
import { PanelShell, ValueBadge } from "./ui/PanelShell";

const confidenceTone = (value) => {
  switch (String(value).toUpperCase()) {
    case "HIGH":
      return "emerald";
    case "MEDIUM":
      return "violet";
    default:
      return "amber";
  }
};

const renderBreakdownValue = (value) => {
  if (typeof value === "number") {
    return value;
  }
  if (typeof value === "boolean") {
    return value ? "PASS" : "FAIL";
  }
  if (value && typeof value === "object" && "score" in value) {
    return value.score;
  }
  return String(value ?? "--");
};

export default function SignalPanel({ signal }) {
  const score = numeric(signal?.score, 0);
  const threshold = numeric(signal?.threshold, 70);
  const confidence = signal?.confidence || "LOW";
  const aboveThreshold = score >= threshold;
  const width = `${Math.min(100, Math.max(6, score))}%`;
  const breakdown = Object.entries(signal?.breakdown || {}).slice(0, 6);

  return (
    <PanelShell
      title="Signal Engine"
      subtitle="Layer 4"
      accent={aboveThreshold ? "emerald" : "violet"}
      action={<ValueBadge label="Confidence" value={confidence} tone={confidenceTone(confidence)} />}
      className="h-full"
    >
      <div className="grid gap-4 lg:grid-cols-[1.2fr_0.8fr]">
        <div className={`rounded-[24px] border p-5 ${aboveThreshold ? "border-emerald-400/20 bg-emerald-400/8 shadow-signal" : "border-violet-400/20 bg-violet-400/8"}`}>
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-xs uppercase tracking-[0.24em] text-slate-400">Current signal score</p>
              <div className="mt-3 flex items-end gap-3">
                <span className="font-display text-5xl font-semibold text-white">{score}</span>
                <span className="pb-2 text-sm text-slate-400">/ 100</span>
              </div>
            </div>
            <div className={`rounded-full border px-4 py-2 text-sm font-semibold ${aboveThreshold ? "animate-signal-pulse border-emerald-300/30 bg-emerald-300/12 text-emerald-50" : "border-white/10 bg-white/[0.04] text-slate-200"}`}>
              {aboveThreshold ? "Signal Armed" : "Standby"}
            </div>
          </div>
          <div className="mt-6 h-3 overflow-hidden rounded-full bg-ink-950/75">
            <div className={`h-full rounded-full ${aboveThreshold ? "bg-gradient-to-r from-emerald-300 via-cyan-300 to-signal-blue" : "bg-gradient-to-r from-violet-400 to-signal-blue"}`} style={{ width }} />
          </div>
          <div className="mt-4 flex flex-wrap gap-3">
            <ValueBadge label="Threshold" value={threshold} tone="amber" />
            <ValueBadge label="Direction" value={signal?.direction || "NONE"} tone={aboveThreshold ? "emerald" : "slate"} />
            <ValueBadge label="Delta" value={`${score - threshold >= 0 ? "+" : ""}${score - threshold}`} tone={aboveThreshold ? "emerald" : "amber"} />
          </div>
        </div>
        <div className="rounded-[24px] border border-white/8 bg-ink-900/55 p-5">
          <div className="text-xs uppercase tracking-[0.24em] text-slate-400">Scoring breakdown</div>
          <div className="mt-4 space-y-3">
            {breakdown.length ? (
              breakdown.map(([key, value]) => (
                <div key={key} className="flex items-center justify-between gap-3 rounded-2xl border border-white/6 bg-white/[0.03] px-4 py-3">
                  <span className="text-sm text-slate-300">{titleize(key)}</span>
                  <span className="font-display text-sm font-semibold text-white">{renderBreakdownValue(value)}</span>
                </div>
              ))
            ) : (
              <div className="rounded-2xl border border-dashed border-white/10 px-4 py-8 text-sm text-slate-400">Waiting for enriched breakdown data from the latest decision cycle.</div>
            )}
          </div>
          <div className="mt-4 text-xs uppercase tracking-[0.22em] text-slate-500">Last evaluation {formatTimestamp(signal?.lastSignalTimestamp)}</div>
        </div>
      </div>
    </PanelShell>
  );
}
