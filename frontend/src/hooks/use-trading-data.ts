import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Api, type BacktestParams, type RsiBacktestParams } from "@/lib/api";

// ── Live data queries ────────────────────────────────────────────────────────
export function useCandles(tf: string, count = 240) {
  return useQuery({
    queryKey: ["candles", tf, count],
    queryFn: () => Api.candles(tf, count),
    refetchInterval: 60_000,
  });
}

export function useXauStatus() {
  return useQuery({
    queryKey: ["xau-status"],
    queryFn: Api.xauStatus,
    refetchInterval: 3_000,
  });
}

export function useXauStats() {
  return useQuery({
    queryKey: ["xau-stats"],
    queryFn: Api.xauStats,
    refetchInterval: 5_000,
  });
}

export function useXauConditions() {
  return useQuery({
    queryKey: ["xau-conditions"],
    queryFn: Api.xauConditions,
    refetchInterval: 3_000,
  });
}

export function useXauSignals(limit = 20) {
  return useQuery({
    queryKey: ["xau-signals", limit],
    queryFn: () => Api.xauSignals(limit),
    refetchInterval: 10_000,
  });
}

export function useXauLog(limit = 100) {
  return useQuery({
    queryKey: ["xau-log", limit],
    queryFn: () => Api.xauLog(limit),
    refetchInterval: 5_000,
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
export function useRunBacktest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (params: BacktestParams) => Api.runBacktest(params),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["backtest-history"] }),
  });
}

// RSI EMA forex engine backtest (POST /backtest)
export function useRunRsiBacktest() {
  return useMutation({
    mutationFn: (params: RsiBacktestParams) => Api.runRsiBacktest(params),
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
      strategy: "rsi-ema" | "xau-scalp";
    }) => (action === "START" ? Api.startBot(strategy) : Api.stopBot(strategy)),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["bot-status"] });
      qc.invalidateQueries({ queryKey: ["xau-status"] });
    },
  });
}
