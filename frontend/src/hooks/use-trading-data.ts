import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Api, type RsiBacktestParams, type VwapStBacktestParams } from "@/lib/api";

// ── Live data queries ────────────────────────────────────────────────────────
export function useCandles(tf: string, count = 240) {
  return useQuery({
    queryKey: ["candles", tf, count],
    queryFn: () => Api.candles(tf, count),
    refetchInterval: 60_000,
  });
}

export function useLivePrice() {
  return useQuery({
    queryKey: ["live-price"],
    queryFn: Api.livePrice,
    refetchInterval: 3_000,
  });
}

// ── BTC (Binance data mirror) ────────────────────────────────────────────────
export function useBtcCandles(tf: string, count = 240) {
  return useQuery({
    queryKey: ["btc-candles", tf, count],
    queryFn: () => Api.btcCandles(tf, count),
    refetchInterval: 60_000,
  });
}

export function useBtcLivePrice() {
  return useQuery({
    queryKey: ["btc-live-price"],
    queryFn: Api.btcLivePrice,
    refetchInterval: 3_000,
  });
}

export function useMarketStatus() {
  return useQuery({
    queryKey: ["market-status"],
    queryFn: Api.marketStatus,
    refetchInterval: 30_000,
  });
}

export function useBotStatus() {
  return useQuery({
    queryKey: ["bot-status"],
    queryFn: Api.botStatus,
    refetchInterval: 5_000,
  });
}

export function useBacktestHistory() {
  return useQuery({
    queryKey: ["backtest-history"],
    queryFn: () => Api.backtestHistory(50),
    retry: false,
    refetchInterval: 30_000,
  });
}

export function useLiveSignals(limit = 100) {
  return useQuery({
    queryKey: ["live-signals", limit],
    queryFn: () => Api.liveSignals(limit),
    refetchInterval: 15_000,
  });
}

// ── Mutations ────────────────────────────────────────────────────────────────

// RSI EMA forex engine backtest (POST /backtest)
export function useRunRsiBacktest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (params: RsiBacktestParams) => Api.runRsiBacktest(params),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["backtest-history"] }),
  });
}

// VWAP + Supertrend backtest (POST /api/vwap-st/backtest)
export function useRunVwapStBacktest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (params: VwapStBacktestParams) => Api.runVwapStBacktest(params),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["backtest-history"] }),
  });
}

// BTC RSI EMA backtest (POST /api/btc/backtest) — same param shape as RSI EMA
export function useRunBtcBacktest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (params: RsiBacktestParams) => Api.runBtcBacktest(params),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["backtest-history"] }),
  });
}

export function useBotControl() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      action,
      strategy,
    }: {
      action: "START" | "STOP";
      strategy: "rsi-ema" | "vwap-st";
    }) => (action === "START" ? Api.startBot(strategy) : Api.stopBot(strategy)),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["bot-status"] });
    },
  });
}
