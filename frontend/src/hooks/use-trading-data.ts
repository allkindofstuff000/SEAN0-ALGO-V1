import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { MOCK_BACKTESTS, MOCK_SIGNALS, MOCK_DECISION_LOGS } from "../lib/mock-data";

// Simulated network delay
const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

export function useBacktests() {
  return useQuery({
    queryKey: ["backtests"],
    queryFn: async () => {
      await delay(400);
      return MOCK_BACKTESTS;
    },
  });
}

export function useSignals() {
  return useQuery({
    queryKey: ["signals"],
    queryFn: async () => {
      await delay(300);
      return MOCK_SIGNALS;
    },
  });
}

export function useDecisionLogs(filter: string = "ALL") {
  return useQuery({
    queryKey: ["decision-logs", filter],
    queryFn: async () => {
      await delay(200);
      if (filter === "ALL") return MOCK_DECISION_LOGS;
      return MOCK_DECISION_LOGS.filter(log => log.source.includes(filter));
    },
  });
}

export function useRunBacktest() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (params: any) => {
      await delay(1200); // Simulate heavy computation
      const newBacktest = {
        id: `#${MOCK_BACKTESTS.length + 40}`,
        date: new Date().toISOString().split('T')[0],
        tradeCount: Math.floor(Math.random() * 200) + 50,
        winRate: Number((Math.random() * 20 + 55).toFixed(1)),
        profitFactor: Number((Math.random() * 1.5 + 1.2).toFixed(2)),
        pnlAmount: Number((Math.random() * 5000 + 500).toFixed(2)),
        params: `SL: ${params.sl} ATR / TP: ${params.tp} ATR / Risk: ${params.risk}%`
      };
      // In a real app we'd post this to the server
      MOCK_BACKTESTS.unshift(newBacktest);
      return newBacktest;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["backtests"] });
    },
  });
}

export function useBotControl() {
  return useMutation({
    mutationFn: async ({ action, strategy }: { action: "START" | "STOP", strategy: string }) => {
      await delay(500);
      return { success: true, action, strategy };
    }
  });
}
