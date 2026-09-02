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

export type LivePrice = {
  price: number;
  time: number;
  initialized: boolean;
};

export type MarketStatus = {
  open: boolean;
  closed: boolean;
  reason: string | null;
  nextOpen: string | null;
};

export type BotStatus = {
  rsiEma: { running: boolean; pid: number | null; startedAt: string | null };
  vwapSt: { running: boolean; pid: number | null; startedAt: string | null };
  rsiEth: Record<string, any>;
  anyRunning: boolean;
  market: MarketStatus;
};

// ── RSI EMA forex backtest (POST /backtest) ──────────────────────────────────
export type RsiBacktestParams = {
  start_date?: string | null;
  end_date?: string | null;
  sl_candles: number; // ×0.3 → ATR mult (5 → 1.5×ATR)
  tp_candles: number; // ×0.3 → ATR mult (10 → 3.0×ATR)
  starting_balance: number;
  risk_per_trade_pct: number; // 1–10
  detection_lag_seconds?: number; // honest M1-drift fill N sec after the signal bar (models the 60s poll)
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
  detection_lag_seconds?: number;
  data_completeness_pct?: number;
  data_missing_bars?: number;
  data_gap_days?: { date: string; actual: number; missing: number }[];
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

// ── VWAP + Supertrend backtest (POST /api/vwap-st/backtest) ─────────────────
export type VwapStBacktestParams = {
  start_date?: string | null;
  end_date?: string | null;
  starting_balance: number;
  risk_per_trade_pct: number;
  st_period: number;
  st_mult: number;
  sl_atr: number;
  tp_atr: number;
  max_hold_bars: number;
  detection_lag_seconds?: number; // honest M1-drift fill N sec after the signal bar (models the 60s poll)
};

// VWAP+ST backtest returns the same shape as the RSI EMA one (metrics + trades + equity_curve)
export type VwapStBacktestResult = RsiBacktestResult;

export type BacktestHistoryItem = {
  _id: string;
  saved_at: string;
  trade_count: number;
  params: {
    start_date?: string;
    end_date?: string;
    sl_candles?: number;
    tp_candles?: number;
    starting_balance?: number;
    risk_per_trade_pct?: number;
    strategy?: string;
    [k: string]: any;
  };
  metrics: Record<string, any>;
};

export type LiveSignal = {
  _id: string;
  sent_at?: string;
  timestamp?: number;
  candle_time_utc?: string | null;
  symbol: string;
  direction: string;
  entry_price: number;
  stop_loss?: number | null;
  take_profit?: number | null;
  atr?: number;
  score?: number;
  strength?: string;
  session?: string;
  market_regime?: string;
  reason?: string;
  signal_kind?: string;
  strategy?: string;
  strategyName?: string;
  telegram_sent?: boolean;
  outcome?: string | null;
  exit_price?: number | null;
  outcome_note?: string | null;
  // Extra context for the click-to-expand detail view
  score_threshold?: number;
  regime_confidence?: number;
  trend_alignment?: boolean;
  price_trigger?: boolean;
  rsi_filter?: boolean;
  atr_expansion?: boolean;
  marked_at?: string | null;
};

// ── Endpoints ────────────────────────────────────────────────────────────────
export const Api = {
  candles: (tf: string, count = 240) =>
    apiGet<{ candles: Candle[]; granularity: string; source: string }>(
      `/api/candles/${tf}?count=${count}`,
    ),
  livePrice: () => apiGet<LivePrice>("/api/live/price"),
  marketStatus: () => apiGet<MarketStatus>("/api/market/status"),
  botStatus: () => apiGet<BotStatus>("/api/bot/status"),
  backtestHistory: (limit = 50) =>
    apiGet<{ reports: BacktestHistoryItem[]; count: number; mongo_available: boolean }>(`/backtest/history?limit=${limit}`),
  backtestReport: (id: string) => apiGet<RsiBacktestResult & { params?: any; saved_at?: string }>(`/backtest/history/${id}`),
  runRsiBacktest: (p: RsiBacktestParams) => apiPost<RsiBacktestResult>("/backtest", p),
  runVwapStBacktest: (p: VwapStBacktestParams) => apiPost<VwapStBacktestResult>("/api/vwap-st/backtest", p),
  liveSignals: (limit = 100) => apiGet<{ signals: LiveSignal[]; count: number }>(`/signals?limit=${limit}`),
  startBot: (strategy: "rsi-ema" | "vwap-st") =>
    apiPost<{ status: string; message: string }>(`/api/bot/${strategy}/start`),
  stopBot: (strategy: "rsi-ema" | "vwap-st") =>
    apiPost<{ status: string; message: string }>(`/api/bot/${strategy}/stop`),

  // ── BTC RSI EMA (Binance data mirror) ──────────────────────────────────────
  btcCandles: (tf: string, count = 240) =>
    apiGet<{ candles: Candle[]; granularity: string; source: string }>(
      `/api/btc/candles/${tf}?count=${count}`,
    ),
  btcLivePrice: () => apiGet<LivePrice>("/api/btc/price"),
  runBtcBacktest: (p: RsiBacktestParams) => apiPost<RsiBacktestResult>("/api/btc/backtest", p),
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

// BTC live candle stream (Binance data mirror, polling SSE — /api/btc/stream)
export function openBtcCandleStream(
  tf: string,
  onEvent: (e: StreamEvent) => void,
  onError?: (e: Event) => void,
): EventSource {
  const es = new EventSource(apiUrl(`/api/btc/stream/${tf}`));
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
