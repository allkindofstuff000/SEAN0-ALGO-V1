// Real backend API client for the SEAN-ALGO FastAPI server.
// The dashboard is served by the same FastAPI app, so all paths are same-origin.
// For local dev against the VPS, set VITE_API_BASE=http://72.62.200.207:8000

export const API_BASE: string =
  (import.meta as any).env?.VITE_API_BASE?.replace(/\/$/, "") || "";

export function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(apiUrl(path), {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = "";
    try {
      const j = await res.json();
      detail = j?.detail || JSON.stringify(j);
    } catch {
      detail = await res.text().catch(() => "");
    }
    throw new Error(`${res.status} ${res.statusText}${detail ? ` — ${detail}` : ""}`);
  }
  return res.json() as Promise<T>;
}

export const apiGet = <T>(path: string) => http<T>(path);
export const apiPost = <T>(path: string, body?: unknown) =>
  http<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined });

// ── Types ──────────────────────────────────────────────────────────────────
export type Candle = {
  time: number; // unix seconds
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
  complete?: boolean;
};

export type Condition = {
  active: boolean;
  headline: string;
  detail: string;
  badge: string;
  allowed?: boolean;
  session?: string;
  zoneName?: string;
  countdown?: { sessionName?: string; startsIn_minutes?: number };
};

export type Conditions = {
  session: Condition;
  bias15m: Condition;
  zone5m: Condition;
  sslSweep: Condition;
  displacement: Condition;
  cisd: Condition;
  entryFVG: Condition;
  pdaCheck: Condition;
};

export type XauStatus = {
  initialized: boolean;
  paused: boolean;
  candleCount: number;
  livePrice: { price: number; time: number };
  conditions: Conditions;
};

export type XauStats = {
  totalSignals: number;
  strongSignals: number;
  mediumSignals: number;
  lastSignalTime: number | null;
  candleCount: number;
  initialized: boolean;
};

export type XauSignal = {
  ts?: number;
  time?: string;
  price?: number;
  direction?: "BUY" | "SELL" | string;
  strength?: string;
  score?: number;
  reason?: string;
  session?: string;
};

export type DecisionLogRow = {
  ts: number;
  candleTime: number;
  price: number;
  reason: string;
  decision: string;
  score: number;
  strength: string | null;
  direction: string | null;
  conditions: Record<string, boolean>;
  session: string;
  bias: string;
};

export type MarketStatus = {
  open: boolean;
  closed: boolean;
  reason: string | null;
  nextOpen: string | null;
};

export type BotStatus = {
  rsiEma: { running: boolean; pid: number | null; startedAt: string | null };
  xauScalp: { running: boolean; paused: boolean; startedAt: string | null };
  rsiEth: Record<string, any>;
  anyRunning: boolean;
  market: MarketStatus;
};

export type BacktestMetrics = {
  totalTrades?: number;
  winRate?: number;
  profitFactor?: number;
  finalBalance?: number;
  netProfit?: number;
  maxDrawdown?: number;
  [k: string]: any;
};

export type BacktestResult = {
  metrics: BacktestMetrics;
  trades: any[];
  equity_curve: any[];
  mongo_id?: string;
  error?: string;
};

export type BacktestParams = {
  start_date?: string | null;
  end_date?: string | null;
  starting_balance: number;
  risk_per_trade_pct: number;
  max_hold_bars?: number;
};

// ── RSI EMA forex backtest (POST /backtest) ──────────────────────────────────
export type RsiBacktestParams = {
  start_date?: string | null;
  end_date?: string | null;
  sl_candles: number; // ×0.3 → ATR mult (5 → 1.5×ATR)
  tp_candles: number; // ×0.3 → ATR mult (10 → 3.0×ATR)
  starting_balance: number;
  risk_per_trade_pct: number; // 1–10
};

export type RsiMetrics = {
  total_trades: number;
  wins: number;
  losses: number;
  win_rate: number;
  profit_factor: number;
  average_r: number;
  max_drawdown_r: number;
  ending_balance: number;
};

export type RsiTrade = {
  timestamp: string;
  entry_timestamp: string;
  exit_timestamp: string;
  direction: "BUY" | "SELL";
  entry_price: number;
  exit_price: number;
  sl: number;
  tp: number;
  result: "WIN" | "LOSS";
  R_multiple: number;
  position_size: number;
  pnl: number;
  equity_before: number;
  equity_after: number;
  rsi: number;
  atr: number;
  reason: string;
  exit_reason: string;
  bars_held: number;
};

export type RsiBacktestResult = {
  metrics: RsiMetrics;
  trades: RsiTrade[];
  equity_curve: { trade: number; equity: number; ts: string }[];
  mongo_id?: string;
  error?: string;
};

// ── Endpoints ────────────────────────────────────────────────────────────────
export const Api = {
  candles: (tf: string, count = 240) =>
    apiGet<{ candles: Candle[]; granularity: string; source: string }>(
      `/api/candles/${tf}?count=${count}`,
    ),
  xauStatus: () => apiGet<XauStatus>("/api/xau-scalp/status"),
  xauStats: () => apiGet<XauStats>("/api/xau-scalp/stats"),
  xauConditions: () => apiGet<Conditions>("/api/xau-scalp/conditions"),
  xauSignals: (limit = 20) =>
    apiGet<{ signals: XauSignal[] }>(`/api/xau-scalp/signals/history?limit=${limit}`),
  xauLog: (limit = 100) =>
    apiGet<{ log: DecisionLogRow[] }>(`/api/xau-scalp/log?limit=${limit}`),
  marketStatus: () => apiGet<MarketStatus>("/api/market/status"),
  botStatus: () => apiGet<BotStatus>("/api/bot/status"),
  backtestHistory: () => apiGet<any>("/backtest/history"),
  runBacktest: (p: BacktestParams) => apiPost<BacktestResult>("/api/xau-scalp/backtest", p),
  runRsiBacktest: (p: RsiBacktestParams) => apiPost<RsiBacktestResult>("/backtest", p),
  startBot: (strategy: "rsi-ema" | "xau-scalp") =>
    apiPost<{ status: string; message: string }>(`/api/bot/${strategy}/start`),
  stopBot: (strategy: "rsi-ema" | "xau-scalp") =>
    apiPost<{ status: string; message: string }>(`/api/bot/${strategy}/stop`),
};

// ── SSE helper for live candle stream ────────────────────────────────────────
export type StreamEvent =
  | { type: "init"; candles: Record<string, Candle[]> }
  | { type: "tick"; timeframe: string; candles: Record<string, Candle> }
  | { type: "candle"; timeframe: string; candle: Candle }
  | { type: "status"; status: any }
  | { type: "heartbeat"; time: number };

export function openCandleStream(
  tf: string,
  onEvent: (e: StreamEvent) => void,
  onError?: (e: Event) => void,
): EventSource {
  const es = new EventSource(apiUrl(`/api/stream/${tf}`));
  es.onmessage = (m) => {
    try {
      onEvent(JSON.parse(m.data));
    } catch {
      /* ignore malformed */
    }
  };
  if (onError) es.onerror = onError;
  return es;
}
