import type { LiveSignal } from "@/lib/api";

/** Per-strategy live scorecard computed from /signals (newest-first list). */
export interface StrategyStats {
  tag: string;
  label: string;
  symbol: string;
  wins: number;
  losses: number;
  expired: number;
  open: number;
  resolved: number;
  winRate: number;   // 0-100 over WIN+LOSS
  netR: number;      // sum of R over WIN+LOSS (exit vs entry, scaled by |entry-stop|)
  firstAt?: string;  // oldest signal in the window (ISO or "YYYY-MM-DD HH:MM:SS")
}

/** Display order + labels. Untagged legacy gold docs count as rsi-ema. */
export const STRATEGY_ORDER: { tag: string; label: string; symbol: string }[] = [
  { tag: "rsi-ema", label: "Gold RSI EMA", symbol: "XAUUSD" },
  { tag: "vwap-st", label: "Gold VWAP+ST", symbol: "XAUUSD" },
  { tag: "macd-gold", label: "Gold MACD", symbol: "XAUUSD" },
  { tag: "rsi-btc", label: "BTC RSI EMA", symbol: "BTCUSD" },
  { tag: "rsi-eth", label: "ETH RSI EMA", symbol: "ETHUSD" },
  { tag: "rsi-sol", label: "SOL RSI EMA", symbol: "SOLUSD" },
  { tag: "nyorb-eth", label: "ETH NY Range", symbol: "ETHUSD" },
];

export function signalR(s: LiveSignal): number | null {
  if (!s.outcome || s.exit_price == null || s.entry_price == null || s.stop_loss == null) return null;
  const isBuy = (s.direction || "").toUpperCase() === "BUY";
  const pts = isBuy ? s.exit_price - s.entry_price : s.entry_price - s.exit_price;
  const risk = Math.abs(s.entry_price - s.stop_loss);
  if (!Number.isFinite(risk) || risk <= 0) return null;
  return pts / risk;
}

export function strategyTag(s: LiveSignal): string {
  const tag = (s.strategy || "").toLowerCase();
  if (tag) return tag;
  const blob = `${s.symbol || ""} ${s.strategyName || ""} ${s.signal_kind || ""}`.toLowerCase();
  if (blob.includes("nyorb")) return "nyorb-eth";
  if (blob.includes("vwap") || blob.includes("supertrend")) return "vwap-st";
  if (blob.includes("macd")) return "macd-gold";
  if (blob.includes("btc")) return "rsi-btc";
  if (blob.includes("sol")) return "rsi-sol";
  if (blob.includes("eth")) return "rsi-eth";
  return "rsi-ema";
}

export function computeStrategyStats(signals: LiveSignal[]): StrategyStats[] {
  const by = new Map<string, StrategyStats>();
  for (const def of STRATEGY_ORDER) {
    by.set(def.tag, { ...def, wins: 0, losses: 0, expired: 0, open: 0, resolved: 0, winRate: 0, netR: 0 });
  }
  for (const s of signals) {
    const tag = strategyTag(s);
    let st = by.get(tag);
    if (!st) {
      st = { tag, label: s.strategyName || tag, symbol: s.symbol || "", wins: 0, losses: 0, expired: 0, open: 0, resolved: 0, winRate: 0, netR: 0 };
      by.set(tag, st);
    }
    const t = s.sent_at || s.candle_time_utc || "";
    if (t && (!st.firstAt || t < st.firstAt)) st.firstAt = t;
    const o = (s.outcome || "").toUpperCase();
    if (o === "WIN") st.wins += 1;
    else if (o === "LOSS") st.losses += 1;
    else if (o === "EXPIRED") st.expired += 1;
    else st.open += 1;
    if (o === "WIN" || o === "LOSS") {
      const r = signalR(s);
      if (r != null) st.netR += r;
    }
  }
  for (const st of by.values()) {
    st.resolved = st.wins + st.losses;
    st.winRate = st.resolved ? (st.wins / st.resolved) * 100 : 0;
    st.netR = Math.round(st.netR * 10) / 10;
  }
  return Array.from(by.values());
}
