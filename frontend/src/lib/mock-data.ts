// Simulated trading data for the mockup
export type BacktestResult = {
  id: string;
  date: string;
  tradeCount: number;
  winRate: number;
  profitFactor: number;
  pnlAmount: number;
  params: string;
};

export type Signal = {
  id: string;
  time: string;
  source: string;
  type: "BUY" | "SELL";
  price: number;
  context: string;
};

export type DecisionLogEntry = {
  id: string;
  timestamp: string;
  source: string;
  signal: string;
  context: string;
};

export const MOCK_BACKTESTS: BacktestResult[] = [
  { id: "#39", date: "2023-10-25", tradeCount: 142, winRate: 68.5, profitFactor: 2.1, pnlAmount: 4250.00, params: "SL: 1.5 ATR / TP: 3.0 ATR / Risk: 2%" },
  { id: "#38", date: "2023-10-24", tradeCount: 110, winRate: 65.2, profitFactor: 1.8, pnlAmount: 2800.50, params: "SL: 1.5 ATR / TP: 2.5 ATR / Risk: 2%" },
  { id: "#37", date: "2023-10-22", tradeCount: 156, winRate: 71.0, profitFactor: 2.4, pnlAmount: 5120.25, params: "SL: 2.0 ATR / TP: 4.0 ATR / Risk: 3%" },
  { id: "#36", date: "2023-10-20", tradeCount: 89, winRate: 58.4, profitFactor: 1.4, pnlAmount: 950.00, params: "SL: 1.0 ATR / TP: 2.0 ATR / Risk: 1%" },
];

export const MOCK_SIGNALS: Signal[] = [
  { id: "1", time: "14:23:45", source: "RSI EMA", type: "BUY", price: 1984.50, context: "RSI Oversold + EMA Cross" },
  { id: "2", time: "12:10:12", source: "XAU SCALP", type: "SELL", price: 1991.20, context: "5M FVG + 1M BOS" },
  { id: "3", time: "09:45:00", source: "CRYPTO", type: "BUY", price: 34520.00, context: "Liquidity Sweep" },
  { id: "4", time: "08:30:22", source: "RSI EMA", type: "SELL", price: 1988.10, context: "RSI Overbought" },
];

export const MOCK_DECISION_LOGS: DecisionLogEntry[] = [
  { id: "l1", timestamp: "[22:00:49]", source: "RSI-EMA XAU", signal: "0 4493.685", context: "SNAPSHOT RSI-- ATR-- Volatility low, waiting for expansion" },
  { id: "l2", timestamp: "[22:01:15]", source: "ETH-RSI-15", signal: "0 3450.21", context: "EVALUATE No valid signal detected on 15M TF" },
  { id: "l3", timestamp: "[22:05:00]", source: "CLR SCALP", signal: "+1 1984.50", context: "SIGNAL EXECUTED Buy order placed. Target 1990.00, Stop 1980.00" },
  { id: "l4", timestamp: "[22:15:33]", source: "RSI-EMA XAU", signal: "0 4494.120", context: "SNAPSHOT RSI++ ATR++ Monitor for pullback" },
  { id: "l5", timestamp: "[22:20:10]", source: "CLR SCALP", signal: "-1 1990.00", context: "TAKE PROFIT HIT Trade closed successfully." },
];
