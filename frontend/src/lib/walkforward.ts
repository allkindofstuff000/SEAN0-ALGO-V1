import type { RsiMetrics } from "@/lib/api";

// Split a [start,end] date window into train/test at trainFrac (default 60/40).
export function splitWindow(start: string, end: string, trainFrac = 0.6): string {
  const s = new Date(start + "T00:00:00Z").getTime();
  const e = new Date(end + "T00:00:00Z").getTime();
  if (!Number.isFinite(s) || !Number.isFinite(e) || e <= s) return start;
  const splitMs = s + (e - s) * trainFrac;
  return new Date(splitMs).toISOString().split("T")[0];
}

export type WfTone = "good" | "bad" | "warn" | "neutral";
export type WfVerdict = { label: string; tone: WfTone; detail: string };

// Compare in-sample (train) vs out-of-sample (test) to judge robustness.
export function walkForwardVerdict(
  train?: RsiMetrics | null,
  test?: RsiMetrics | null,
  startBal = 5000,
): WfVerdict {
  if (!train || !test) return { label: "—", tone: "neutral", detail: "" };
  const trainNet = (train.ending_balance ?? startBal) - startBal;
  const testNet = (test.ending_balance ?? startBal) - startBal;
  const trainWin = train.win_rate ?? 0;
  const testWin = test.win_rate ?? 0;

  if (trainNet > 0 && testNet > 0) {
    if (testWin >= trainWin - 12)
      return { label: "ROBUST", tone: "good", detail: "Edge holds out-of-sample — the most trustworthy result. Safe to rely on." };
    return { label: "HOLDS (weaker)", tone: "warn", detail: "Still profitable out-of-sample but degraded vs training — usable, but keep an eye on it." };
  }
  if (trainNet > 0 && testNet <= 0)
    return { label: "OVERFIT", tone: "bad", detail: "Profitable in training but LOST out-of-sample — likely curve-fit to the past. Don't trust it live." };
  if (trainNet <= 0 && testNet > 0)
    return { label: "INCONCLUSIVE", tone: "warn", detail: "Lost in training, won in test — noisy / small sample. Not reliable evidence of an edge." };
  return { label: "NO EDGE", tone: "bad", detail: "Unprofitable in both halves — no edge here." };
}
