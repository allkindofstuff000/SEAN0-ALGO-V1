import { AnimatePresence, motion } from "framer-motion";
import {
  Activity,
  BarChart3,
  BrainCircuit,
  CandlestickChart,
  LayoutDashboard,
  Radar,
  ScrollText,
  Settings2,
  ShieldAlert,
  Target,
} from "lucide-react";
import { Suspense, lazy, startTransition, useDeferredValue, useEffect, useMemo, useState } from "react";

import Navbar from "./components/Navbar";
import Sidebar from "./components/Sidebar";
import {
  extractApiError,
  fetchDashboardSnapshot,
  saveRiskConfig,
  sendControlAction,
  updateLearningControl,
} from "./services/api";

const DashboardPage = lazy(() => import("./pages/Dashboard"));
const SignalsPage = lazy(() => import("./pages/Signals"));
const StrategyPage = lazy(() => import("./pages/Strategy"));
const PerformancePage = lazy(() => import("./pages/Performance"));

const POLL_INTERVAL_MS = 3000;

const NAV_ITEMS = [
  { id: "dashboard", page: "dashboard", label: "Dashboard", description: "Overview", icon: LayoutDashboard },
  { id: "market", page: "dashboard", label: "Market Intelligence", description: "Live context", icon: Radar },
  { id: "signals", page: "signals", label: "Signals", description: "Engine feed", icon: Activity },
  { id: "strategy", page: "strategy", label: "Strategy Engine", description: "Adaptive controls", icon: BrainCircuit },
  { id: "risk", page: "strategy", label: "Risk Manager", description: "Protection layer", icon: ShieldAlert },
  { id: "backtest", page: "performance", label: "Backtesting", description: "Historical validation", icon: CandlestickChart },
  { id: "wfo", page: "performance", label: "Walk Forward Optimization", description: "Windowed validation", icon: Target },
  { id: "performance", page: "performance", label: "Performance", description: "Analytics", icon: BarChart3 },
  { id: "logs", page: "signals", label: "Decision Logs", description: "Trace viewer", icon: ScrollText },
  { id: "settings", page: "strategy", label: "Settings", description: "System tuning", icon: Settings2 },
];

const EMPTY_SNAPSHOT = {
  status: null,
  marketState: null,
  signal: null,
  signals: { signals: [], count: 0 },
  performance: null,
  decisionLogs: { logs: [], count: 0 },
  learning: null,
  risk: null,
  backtest: null,
  wfo: null,
};

export default function App() {
  const [snapshot, setSnapshot] = useState(EMPTY_SNAPSHOT);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [activeNav, setActiveNav] = useState("dashboard");
  const [pendingControl, setPendingControl] = useState("");
  const [pendingLearning, setPendingLearning] = useState("");
  const [savingRisk, setSavingRisk] = useState(false);

  const deferredLogs = useDeferredValue(snapshot.decisionLogs?.logs || []);
  const deferredSignals = useDeferredValue(snapshot.signals?.signals || []);
  const deferredPerformance = useDeferredValue(snapshot.performance);
  const deferredBacktest = useDeferredValue(snapshot.backtest);
  const deferredWfo = useDeferredValue(snapshot.wfo);

  useEffect(() => {
    let active = true;
    let timerId;

    const poll = async (isInitial = false) => {
      if (!active) {
        return;
      }

      if (isInitial) {
        setLoading(true);
      }

      try {
        const next = await fetchDashboardSnapshot();
        if (!active) {
          return;
        }
        startTransition(() => {
          setSnapshot(next);
          setError("");
        });
      } catch (requestError) {
        if (active) {
          setError(extractApiError(requestError));
        }
      } finally {
        if (!active) {
          return;
        }
        if (isInitial) {
          setLoading(false);
        }
        timerId = window.setTimeout(() => {
          void poll(false);
        }, POLL_INTERVAL_MS);
      }
    };

    void poll(true);

    return () => {
      active = false;
      window.clearTimeout(timerId);
    };
  }, []);

  const activeItem = useMemo(() => NAV_ITEMS.find((item) => item.id === activeNav) || NAV_ITEMS[0], [activeNav]);
  const activeSignals = useMemo(
    () => (snapshot.signals?.signals || []).filter((row) => row.signalGenerated || row.status === "OPEN").length,
    [snapshot.signals],
  );

  const refreshSnapshot = async () => {
    try {
      const next = await fetchDashboardSnapshot();
      startTransition(() => {
        setSnapshot(next);
        setError("");
      });
    } catch (requestError) {
      setError(extractApiError(requestError));
    }
  };

  const handleControl = async (action) => {
    setPendingControl(action);
    try {
      await sendControlAction(action);
      await refreshSnapshot();
    } catch (requestError) {
      setError(extractApiError(requestError));
    } finally {
      setPendingControl("");
    }
  };

  const handleRiskSave = async (payload) => {
    setSavingRisk(true);
    try {
      await saveRiskConfig(payload);
      await refreshSnapshot();
    } catch (requestError) {
      setError(extractApiError(requestError));
    } finally {
      setSavingRisk(false);
    }
  };

  const handleLearningAction = async (action) => {
    setPendingLearning(action);
    try {
      await updateLearningControl(action);
      await refreshSnapshot();
    } catch (requestError) {
      setError(extractApiError(requestError));
    } finally {
      setPendingLearning("");
    }
  };

  const sharedPageProps = {
    snapshot: {
      ...snapshot,
      decisionLogs: { ...snapshot.decisionLogs, logs: deferredLogs },
      signals: { ...snapshot.signals, signals: deferredSignals },
      performance: deferredPerformance,
      backtest: deferredBacktest,
      wfo: deferredWfo,
    },
    activeNav,
    activeSignals,
    pendingControl,
    pendingLearning,
    savingRisk,
    onControl: handleControl,
    onRiskSave: handleRiskSave,
    onLearningAction: handleLearningAction,
  };

  const renderPage = () => {
    switch (activeItem.page) {
      case "signals":
        return <SignalsPage {...sharedPageProps} />;
      case "strategy":
        return <StrategyPage {...sharedPageProps} />;
      case "performance":
        return <PerformancePage {...sharedPageProps} />;
      case "dashboard":
      default:
        return <DashboardPage {...sharedPageProps} />;
    }
  };

  return (
    <div className="min-h-screen bg-mega-950 text-zinc-100">
      <div className="relative min-h-screen overflow-hidden">
        <div className="pointer-events-none absolute inset-0 opacity-90">
          <div className="absolute left-[-12%] top-[-10%] h-[380px] w-[380px] rounded-full bg-accent-red/20 blur-[110px]" />
          <div className="absolute right-[-10%] top-[12%] h-[420px] w-[420px] rounded-full bg-accent-orange/14 blur-[140px]" />
          <div className="absolute bottom-[-14%] left-[30%] h-[360px] w-[360px] rounded-full bg-accent-crimson/12 blur-[120px]" />
        </div>

        <div className="relative mx-auto flex min-h-screen max-w-[1880px] flex-col px-3 py-3 lg:flex-row lg:gap-4 lg:px-5 lg:py-5">
          <Sidebar items={NAV_ITEMS} activeItem={activeNav} status={snapshot.status} onNavigate={setActiveNav} />

          <div className="flex min-w-0 flex-1 flex-col gap-4">
            <Navbar
              activeLabel={activeItem.label}
              status={snapshot.status}
            />

            {error ? (
              <motion.div
                initial={{ opacity: 0, y: -10 }}
                animate={{ opacity: 1, y: 0 }}
                className="rounded-[22px] border border-accent-red/35 bg-accent-red/12 px-5 py-4 text-sm text-red-100 shadow-ember-alert"
              >
                API error: {error}
              </motion.div>
            ) : null}

            <AnimatePresence mode="wait">
              <motion.main
                key={activeItem.id}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -10 }}
                transition={{ duration: 0.22, ease: "easeOut" }}
                className="min-w-0"
              >
                <Suspense
                  fallback={
                    <div className="megaboost-panel px-6 py-10 text-sm text-zinc-300">
                      Loading {activeItem.label.toLowerCase()} panel...
                    </div>
                  }
                >
                  {renderPage()}
                </Suspense>
              </motion.main>
            </AnimatePresence>
          </div>
        </div>
      </div>
    </div>
  );
}
