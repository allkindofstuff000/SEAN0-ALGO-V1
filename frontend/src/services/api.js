import axios from "axios";

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || "",
  timeout: 6000,
});

const extractData = async (request) => {
  const response = await request;
  return response.data;
};

export const fetchStatus = () => extractData(api.get("/api/status"));
export const fetchMarketState = () => extractData(api.get("/api/market-state"));
export const fetchSignal = () => extractData(api.get("/api/signal"));
export const fetchSignals = (limit = 20) => extractData(api.get("/api/signals", { params: { limit } }));
export const fetchPerformance = () => extractData(api.get("/api/performance"));
export const fetchDecisionLogs = (limit = 40) => extractData(api.get("/api/decision-logs", { params: { limit } }));
export const fetchLearningState = () => extractData(api.get("/api/learning-state"));
export const fetchRiskState = () => extractData(api.get("/api/risk"));
export const fetchBacktest = () => extractData(api.get("/api/backtest"));
export const fetchWfo = () => extractData(api.get("/api/wfo"));

export const fetchDashboardSnapshot = async () => {
  const [status, marketState, signal, signals, performance, decisionLogs, learning, risk, backtest, wfo] = await Promise.all([
    fetchStatus(),
    fetchMarketState(),
    fetchSignal(),
    fetchSignals(),
    fetchPerformance(),
    fetchDecisionLogs(),
    fetchLearningState(),
    fetchRiskState(),
    fetchBacktest(),
    fetchWfo(),
  ]);

  return {
    status,
    marketState,
    signal,
    signals,
    performance,
    decisionLogs,
    learning,
    risk,
    backtest,
    wfo,
  };
};

export const sendControlAction = (action) => extractData(api.post("/api/control", { action }));
export const saveRiskConfig = (payload) => extractData(api.post("/api/risk-config", payload));
export const updateLearningControl = (action) => extractData(api.post("/api/learning-control", { action }));

export const extractApiError = (error) => {
  if (error?.response?.data?.error) {
    return String(error.response.data.error);
  }
  if (error?.message) {
    return String(error.message);
  }
  return "Unknown API error";
};

export default api;
