import { PageLayout } from "@/components/layout/PageLayout";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useCandles, useLivePrice, useRunRsiBacktest, useLiveSignals, useBacktestHistory, useMarketStatus, useBotStatus } from "@/hooks/use-trading-data";
import { Fragment, useMemo, useState } from "react";
import { Play, BarChart2, History, TrendingUp, Send, Clock } from "lucide-react";
import { useToast } from "@/hooks/use-toast";
import { LiveChart } from "@/components/LiveChart";
import { BacktestResults } from "@/components/BacktestResults";
import { WalkForwardResults } from "@/components/WalkForwardResults";
import { SignalDetail } from "@/components/SignalDetail";
import { SessionPill } from "@/components/SessionPill";
import { computeIndicators } from "@/lib/indicators";
import { Api, type RsiBacktestResult } from "@/lib/api";
import { fmtLocal, TZ_LABEL } from "@/lib/tz";
import { splitWindow } from "@/lib/walkforward";

const TABS = [
  { id: "chart", label: "Chart", icon: BarChart2 },
  { id: "backtest", label: "Backtest", icon: Play },
  { id: "signals", label: "Signal History", icon: History },
];

const TF_MAP: Record<string, string> = { "1M": "M1", "5M": "M5", "15M": "M15", "1H": "H1" };

const isoDaysAgo = (n: number) => {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - n);
  return d.toISOString().split("T")[0];
};

// P&L of a resolved signal: price points ("pips") and R multiple.
function signalPnl(s: {
  direction?: string;
  entry_price?: number;
  exit_price?: number | null;
  stop_loss?: number | null;
  outcome?: string | null;
}): { pips: number; r: number } | null {
  if (!s.outcome || s.exit_price == null || s.entry_price == null) return null;
  const isBuy = (s.direction || "").toUpperCase() === "BUY";
  const pips = isBuy ? s.exit_price - s.entry_price : s.entry_price - s.exit_price;
  const risk = s.stop_loss != null ? Math.abs(s.entry_price - s.stop_loss) : 0;
  const r = risk > 0 ? pips / risk : 0;
  return { pips, r };
}

export default function RsiEma() {
  const { toast } = useToast();
  const [activeTab, setActiveTab] = useState("chart");
  const [activeTF, setActiveTF] = useState("5M");
  const [showEma, setShowEma] = useState(true);
  const [sl, setSl] = useState([1.5]);
  const [tp, setTp] = useState([1.5]);
  const [risk, setRisk] = useState([2]);
  const [lag, setLag] = useState([0]); // detection lag (seconds): 0 / 60 / 120 — honest M1-drift fill
  const [startDate, setStartDate] = useState(isoDaysAgo(90));
  const [endDate, setEndDate] = useState(isoDaysAgo(1));
  const [balance, setBalance] = useState(10000);
  const [result, setResult] = useState<RsiBacktestResult | null>(null);
  const [ranBalance, setRanBalance] = useState(10000);
  const [wf, setWf] = useState<{ train: RsiBacktestResult; test: RsiBacktestResult; split: string } | null>(null);
  const [wfRunning, setWfRunning] = useState(false);
  const [expandedSig, setExpandedSig] = useState<string | null>(null);

  const tf = TF_MAP[activeTF];
  const { data: candleData } = useCandles(tf, 240);
  const { data: livePrice } = useLivePrice();
  const { data: signalsData } = useLiveSignals(100);
  const { data: history } = useBacktestHistory();
  const { data: market } = useMarketStatus();
  const runBacktest = useRunRsiBacktest();
  const [loadingReport, setLoadingReport] = useState<string | null>(null);

  // RSI EMA live bot status (start/stop lives on the Live Bot overview page)
  const { data: botStatus } = useBotStatus();
  const rsiRunning = !!botStatus?.rsiEma?.running;

  const openReport = async (id: string) => {
    setLoadingReport(id);
    try {
      const doc = await Api.backtestReport(id);
      setResult({ metrics: doc.metrics as any, trades: doc.trades || [], equity_curve: doc.equity_curve || [] });
      setRanBalance(Number(doc.params?.starting_balance) || 10000);
    } catch (e: any) {
      toast({ title: "Could not load report", description: String(e.message || e), variant: "destructive" });
    } finally {
      setLoadingReport(null);
    }
  };

  // Only RSI EMA forex runs — filter out VWAP+ST (shown on its own page) and any legacy XAU Scalp runs
  const rsiReports = (history?.reports || []).filter((r) => {
    const strat = r.params?.strategy;
    return strat !== "xau-scalp" && strat !== "vwap-st" && r.metrics?.total_trades != null;
  });

  // Forex-only signal history — exclude ETH/crypto strategies (this is a XAU dashboard)
  const forexSignals = (signalsData?.signals || []).filter((s) => {
    const blob = `${s.symbol || ""} ${s.strategy || ""} ${s.strategyName || ""} ${s.signal_kind || ""}`.toLowerCase();
    return !blob.includes("eth") && !blob.includes("btc") && !blob.includes("crypto");
  });

  // Win/loss summary over resolved signals
  const sigResolved = forexSignals.filter((s) => s.outcome === "WIN" || s.outcome === "LOSS");
  const sigWins = sigResolved.filter((s) => s.outcome === "WIN").length;
  const sigLosses = sigResolved.filter((s) => s.outcome === "LOSS").length;
  const sigWinRate = sigResolved.length ? (sigWins / sigResolved.length) * 100 : 0;
  const sigOpen = forexSignals.length - sigResolved.length;
  const sigNetR = sigResolved.reduce((acc, s) => acc + (signalPnl(s)?.r ?? 0), 0);

  const ind = useMemo(() => computeIndicators(candleData?.candles ?? []), [candleData]);
  const price = livePrice?.price || ind?.price || 0;
  const changeAbs = ind?.changeAbs ?? 0;
  const changePct = ind?.changePct ?? 0;

  const stats = [
    { label: "RSI (14)", val: ind ? ind.rsi.toFixed(1) : "—", color: ind && ind.rsi > 70 ? "text-destructive" : ind && ind.rsi < 30 ? "text-accent" : "text-accent" },
    { label: "EMA 9", val: ind ? ind.ema9.toFixed(2) : "—", color: "text-foreground" },
    { label: "EMA 21", val: ind ? ind.ema21.toFixed(2) : "—", color: "text-foreground" },
    { label: "ATR", val: ind ? ind.atr.toFixed(2) : "—", color: "text-primary" },
  ];

  const rsiParams = (start: string, end: string) => ({
    start_date: start || null,
    end_date: end || null,
    sl_candles: Math.max(1, Math.round(sl[0] / 0.3)),
    tp_candles: Math.max(1, Math.round(tp[0] / 0.3)),
    starting_balance: balance,
    risk_per_trade_pct: risk[0],
    detection_lag_seconds: lag[0] || 0,
  });

  const handleRunTest = () => {
    const bal = balance;
    setWf(null);
    runBacktest.mutate(rsiParams(startDate, endDate), {
      onSuccess: (res) => {
        if (res.error) {
          toast({ title: "Backtest error", description: res.error, variant: "destructive" });
          return;
        }
        setResult(res);
        setRanBalance(bal);
        const m = res.metrics;
        toast({
          title: "Backtest Complete",
          description: `${m.total_trades} trades · ${m.win_rate.toFixed(1)}% win · final $${m.ending_balance.toFixed(2)}`,
        });
      },
      onError: (e: any) => toast({ title: "Backtest failed", description: String(e.message || e), variant: "destructive" }),
    });
  };

  const handleWalkForward = async () => {
    const split = splitWindow(startDate, endDate, 0.6);
    if (split === startDate) {
      toast({ title: "Window too short", description: "Pick a wider date range to split into train/test.", variant: "destructive" });
      return;
    }
    setWfRunning(true);
    setWf(null);
    try {
      const train = await Api.runRsiBacktest(rsiParams(startDate, split));
      if (train.error) throw new Error(train.error);
      const test = await Api.runRsiBacktest(rsiParams(split, endDate));
      if (test.error) throw new Error(test.error);
      setWf({ train, test, split });
      toast({ title: "Walk-Forward Complete", description: `Train ${startDate}→${split} · Test ${split}→${endDate}` });
    } catch (e: any) {
      toast({ title: "Walk-forward failed", description: String(e.message || e), variant: "destructive" });
    } finally {
      setWfRunning(false);
    }
  };

  const marketClosed = market && market.closed;

  return (
    <PageLayout>
      <div className="flex items-center border-b border-border/60 bg-card/50 px-4 shrink-0">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => setActiveTab(id)}
            className={`flex items-center gap-1.5 px-4 py-3 text-xs font-bold uppercase tracking-wider border-b-2 transition-colors ${
              activeTab === id ? "border-primary text-primary" : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            <Icon className="w-3.5 h-3.5" />
            {label}
          </button>
        ))}
      </div>

      {/* Market + Session Pills */}
      <div className="px-4 py-2 border-b border-border/30 shrink-0 flex items-center gap-2 flex-wrap">
        <div className={`inline-flex items-center gap-2 px-3 py-1 rounded-full border text-xs font-bold ${marketClosed ? "bg-secondary/60 border-border text-muted-foreground" : "bg-accent/10 border-accent/20 text-accent"}`}>
          <span className={`w-1.5 h-1.5 rounded-full ${marketClosed ? "bg-yellow-500/80" : "bg-accent animate-pulse"}`} />
          XAUUSD · Forex Market · <span className={marketClosed ? "text-yellow-500" : "text-accent"}>{marketClosed ? (market?.reason || "Closed") : "Open"}</span>
        </div>
        <SessionPill />
      </div>

      {/* Header Card */}
      <div className="mx-4 mt-3 rounded-lg border border-border bg-card px-5 py-3 flex items-center justify-between shrink-0">
        <div>
          <div className="flex items-center gap-2">
            <TrendingUp className="w-4 h-4 text-primary" />
            <span className="font-bold text-lg tracking-tight">XAU/USD</span>
            {rsiRunning && <Badge className="bg-accent/20 text-accent border-accent/40 text-[10px] font-bold px-2 animate-pulse">LIVE</Badge>}
            <Badge className="bg-primary/20 text-primary border-primary/40 text-[10px] font-bold px-2">RSI EMA</Badge>
          </div>
          <p className="text-xs text-muted-foreground mt-0.5">Multi-timeframe RSI + EMA crossover · {activeTF}</p>
        </div>
        <div className="text-right">
          <div className="font-mono text-2xl font-bold tracking-tight">{price ? price.toFixed(2) : "—"}</div>
          <div className={`font-mono text-sm font-bold ${changeAbs >= 0 ? "text-accent" : "text-destructive"}`}>
            {changeAbs >= 0 ? "+" : ""}{changeAbs.toFixed(2)} ({changePct.toFixed(2)}%)
          </div>
        </div>
      </div>

      {/* Stats Row */}
      <div className="mx-4 mt-2 grid grid-cols-4 border border-border rounded-lg overflow-hidden shrink-0">
        {stats.map((s, i) => (
          <div key={s.label} className={`px-4 py-3 flex flex-col gap-1 bg-card ${i > 0 ? "border-l border-border" : ""}`}>
            <span className="text-[10px] text-muted-foreground uppercase font-bold">{s.label}</span>
            <span className={`font-mono text-sm font-bold ${s.color}`}>{s.val}</span>
          </div>
        ))}
      </div>

      <div className="flex-1 mx-4 mt-2 mb-4 min-h-0 overflow-hidden">
        {/* CHART TAB */}
        {activeTab === "chart" && (
          <div className="h-full min-h-[400px] flex flex-col rounded-lg border border-border bg-[#131722] overflow-hidden">
            <div className="flex items-center gap-3 px-3 py-2 border-b border-[#2A2E39] bg-[#1E222D] shrink-0">
              <span className="font-bold text-white text-sm">XAUUSD</span>
              <span className={`text-xs font-mono ${changePct >= 0 ? "text-accent" : "text-destructive"}`}>{changePct >= 0 ? "+" : ""}{changePct.toFixed(2)}%</span>
              <div className="h-3 w-px bg-[#2A2E39]" />
              <div className="flex items-center gap-1">
                {["1M", "5M", "15M", "1H"].map((t) => (
                  <button key={t} onClick={() => setActiveTF(t)} className={`px-2 py-0.5 text-xs font-bold rounded transition-all ${activeTF === t ? "bg-blue-600 text-white" : "text-gray-400 hover:text-white hover:bg-[#2A2E39]"}`}>
                    {t}
                  </button>
                ))}
              </div>
              <div className="h-3 w-px bg-[#2A2E39]" />
              <button onClick={() => setShowEma((v) => !v)} className={`px-2 py-0.5 text-xs font-bold rounded transition-all ${showEma ? "text-yellow-400" : "text-gray-500 hover:text-gray-300"}`}>
                EMA 9 / 21
              </button>
            </div>
            <div className="flex-1 min-h-0">
              <LiveChart tf={tf} showEMA={showEma} />
            </div>
          </div>
        )}

        {/* BACKTEST TAB */}
        {activeTab === "backtest" && (
          <div className="h-full overflow-auto pb-4 space-y-4">
            {/* Config bar */}
            <div className="rounded-lg border border-border bg-card p-4">
              <div className="flex items-center justify-between mb-3">
                <p className="text-xs font-bold uppercase tracking-wider">Backtest Configuration</p>
                <p className="text-[10px] font-mono text-muted-foreground">XAUUSD M5/M15 · live OANDA history · EMA50/200 + RSI breakout</p>
              </div>
              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4 items-end">
                <div className="space-y-1.5">
                  <label className="text-[10px] font-bold text-muted-foreground uppercase">Start Date</label>
                  <Input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} className="h-8 text-xs font-mono bg-background" />
                </div>
                <div className="space-y-1.5">
                  <label className="text-[10px] font-bold text-muted-foreground uppercase">End Date</label>
                  <Input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} className="h-8 text-xs font-mono bg-background" />
                </div>
                <div className="space-y-1.5">
                  <label className="text-[10px] font-bold text-muted-foreground uppercase">Balance (USD)</label>
                  <Input type="number" value={balance} onChange={(e) => setBalance(Number(e.target.value))} className="h-8 text-xs font-mono bg-background" />
                </div>
                <div className="space-y-2">
                  <div className="flex justify-between"><label className="text-[10px] font-bold text-muted-foreground uppercase">SL ATR</label><span className="text-xs font-mono text-primary font-bold">{sl[0].toFixed(1)}x</span></div>
                  <Slider value={sl} onValueChange={setSl} min={0.5} max={5} step={0.1} />
                </div>
                <div className="space-y-2">
                  <div className="flex justify-between"><label className="text-[10px] font-bold text-muted-foreground uppercase">TP ATR</label><span className="text-xs font-mono text-primary font-bold">{tp[0].toFixed(1)}x</span></div>
                  <Slider value={tp} onValueChange={setTp} min={1} max={10} step={0.5} />
                </div>
                <div className="space-y-2">
                  <div className="flex justify-between"><label className="text-[10px] font-bold text-muted-foreground uppercase">Risk</label><span className="text-xs font-mono text-destructive font-bold">{risk[0]}%</span></div>
                  <Slider value={risk} onValueChange={setRisk} min={1} max={10} step={0.5} />
                </div>
                <div className="space-y-2">
                  <div className="flex justify-between"><label className="text-[10px] font-bold text-muted-foreground uppercase" title="Honest detection-lag: fills at the REAL M1 price this many seconds after the signal bar closes (models the ~60s live poll — can help or hurt). 0 = exact next-bar open. Sub-60s rounds down (M1 granularity).">Poll lag</label><span className="text-xs font-mono text-yellow-500 font-bold">{lag[0] === 0 ? "off" : `${lag[0]}s`}</span></div>
                  <Slider value={lag} onValueChange={setLag} min={0} max={120} step={60} />
                </div>
              </div>
              <div className="flex flex-col sm:flex-row gap-2 mt-4">
                <Button className="flex-1 font-bold uppercase tracking-wider" onClick={handleRunTest} disabled={runBacktest.isPending || wfRunning}>
                  {runBacktest.isPending ? "Running backtest…" : <><Play className="w-4 h-4 mr-2" />Run Backtest</>}
                </Button>
                <Button variant="outline" className="flex-1 font-bold uppercase tracking-wider" onClick={handleWalkForward} disabled={runBacktest.isPending || wfRunning} title="Split the window: tune on the first 60%, validate on the unseen last 40%">
                  {wfRunning ? "Running walk-forward…" : <><BarChart2 className="w-4 h-4 mr-2" />Walk-Forward</>}
                </Button>
              </div>
              <p className="text-[10px] text-muted-foreground mt-2">Lag pts = adverse entry slippage to stress-test the polling delay. Walk-Forward splits train/test to catch curve-fitting.</p>
            </div>

            {/* Walk-forward validation */}
            {wf && <WalkForwardResults train={wf.train} test={wf.test} startBalance={balance} splitDate={wf.split} />}

            {/* Results */}
            {runBacktest.isPending ? (
              <div className="rounded-lg border border-border bg-card p-12 text-center">
                <div className="inline-block w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin mb-3" />
                <p className="text-xs text-muted-foreground">Running strategy over OANDA history…</p>
              </div>
            ) : result ? (
              <BacktestResults result={result} startBalance={ranBalance} />
            ) : !wf ? (
              <div className="rounded-lg border border-border border-dashed bg-card/50 p-12 text-center">
                <BarChart2 className="w-10 h-10 text-muted-foreground mx-auto mb-3" />
                <p className="font-bold text-sm uppercase tracking-wider">No backtest yet</p>
                <p className="text-xs text-muted-foreground mt-1">Set your window and risk, then click Run Backtest to see the full equity curve, drawdown, and every trade.</p>
              </div>
            ) : null}

            {/* Backtest history */}
            <div className="rounded-lg border border-border bg-card overflow-hidden">
              <div className="px-4 py-3 border-b border-border bg-secondary/20 flex items-center gap-2">
                <Clock className="w-3.5 h-3.5 text-muted-foreground" />
                <p className="text-xs font-bold uppercase tracking-wider">Backtest History</p>
                <p className="text-[10px] text-muted-foreground font-mono ml-1">click a run to reopen its full report</p>
              </div>
              <div className="overflow-auto max-h-[360px]">
                <Table>
                  <TableHeader className="sticky top-0 bg-card z-10">
                    <TableRow className="hover:bg-transparent border-border">
                      {["Run Date", "Window", "Trades", "Win %", "PF", "Max DD (R)", "Final $", ""].map((h) => (
                        <TableHead key={h} className={`font-mono text-[10px] uppercase text-muted-foreground ${["Trades", "Win %", "PF", "Max DD (R)", "Final $"].includes(h) ? "text-right" : ""}`}>{h}</TableHead>
                      ))}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {rsiReports.length === 0 ? (
                      <TableRow><TableCell colSpan={8} className="text-center py-8 text-xs text-muted-foreground">No saved backtests yet. Run one above — results are stored automatically.</TableCell></TableRow>
                    ) : (
                      rsiReports.map((r) => {
                        const m = r.metrics;
                        const start = Number(r.params?.starting_balance) || 0;
                        const profit = (m.ending_balance ?? 0) - start;
                        return (
                          <TableRow key={r._id} className="border-border/50 hover:bg-secondary/20 cursor-pointer" onClick={() => openReport(r._id)}>
                            <TableCell className="font-mono text-xs text-muted-foreground">{fmtLocal(r.saved_at)}</TableCell>
                            <TableCell className="font-mono text-[11px] text-muted-foreground">{(r.params?.start_date || "?")} → {(r.params?.end_date || "?")}</TableCell>
                            <TableCell className="font-mono text-xs text-right">{m.total_trades ?? r.trade_count ?? 0}</TableCell>
                            <TableCell className="font-mono text-xs text-right font-bold text-primary">{(m.win_rate ?? 0).toFixed(1)}%</TableCell>
                            <TableCell className="font-mono text-xs text-right">{Number.isFinite(m.profit_factor) ? (m.profit_factor ?? 0).toFixed(2) : "∞"}</TableCell>
                            <TableCell className="font-mono text-xs text-right text-destructive">{(m.max_drawdown_r ?? 0).toFixed(2)}</TableCell>
                            <TableCell className={`font-mono text-xs text-right font-bold ${profit >= 0 ? "text-accent" : "text-destructive"}`}>${(m.ending_balance ?? 0).toFixed(0)}</TableCell>
                            <TableCell className="text-right">
                              <span className="text-[10px] text-primary font-bold uppercase">{loadingReport === r._id ? "Loading…" : "View →"}</span>
                            </TableCell>
                          </TableRow>
                        );
                      })
                    )}
                  </TableBody>
                </Table>
              </div>
            </div>
          </div>
        )}

        {/* SIGNAL HISTORY TAB — all fired signals (sent to Telegram) */}
        {activeTab === "signals" && (
          <div className="rounded-lg border border-border bg-card overflow-hidden">
            <div className="px-4 py-3 border-b border-border bg-secondary/20 flex flex-col sm:flex-row sm:items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <Send className="w-3.5 h-3.5 text-muted-foreground" />
                <p className="text-xs font-bold uppercase tracking-wider">Signal History</p>
                <p className="text-[10px] text-muted-foreground font-mono ml-1 hidden md:inline">every fired signal · auto-marked WIN/LOSS when price hits TP or SL</p>
              </div>
              <div className="flex items-center gap-3 text-[11px] font-mono">
                <span className="text-accent font-bold">{sigWins}W</span>
                <span className="text-destructive font-bold">{sigLosses}L</span>
                <span className="text-muted-foreground">{sigOpen} open</span>
                <span className="h-3 w-px bg-border/60" />
                <span className="text-foreground font-bold">{sigWinRate.toFixed(0)}% win</span>
                <span className={`font-bold ${sigNetR >= 0 ? "text-accent" : "text-destructive"}`}>{sigNetR >= 0 ? "+" : ""}{sigNetR.toFixed(2)}R</span>
              </div>
            </div>
            <div className="overflow-auto max-h-[560px]">
              <Table>
                <TableHeader className="sticky top-0 bg-card z-10">
                  <TableRow className="border-border hover:bg-transparent bg-secondary/30">
                    {[`Time (${TZ_LABEL})`, "Strategy", "Symbol", "Dir", "Entry", "SL", "TP", "Score", "TG", "Outcome", "P&L"].map((h) => (
                      <TableHead key={h} className={`font-mono text-[10px] uppercase text-muted-foreground ${["Entry", "SL", "TP", "Score", "P&L"].includes(h) ? "text-right" : ""}`}>{h}</TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {forexSignals.length === 0 ? (
                    <TableRow><TableCell colSpan={11} className="text-center py-10 text-xs text-muted-foreground">No signals fired yet. When the RSI EMA strategy fires and sends a Telegram alert, it appears here.</TableCell></TableRow>
                  ) : (
                    forexSignals.map((s) => {
                      const isBuy = (s.direction || "").toUpperCase() === "BUY";
                      const open = expandedSig === s._id;
                      return (
                        <Fragment key={s._id}>
                        <TableRow className="border-border/50 hover:bg-secondary/20 cursor-pointer" onClick={() => setExpandedSig(open ? null : s._id)} title="Click for full signal details">
                          <TableCell className="font-mono text-[11px] text-muted-foreground"><span className="inline-block w-3 text-primary">{open ? "▾" : "▸"}</span>{fmtLocal(s.sent_at ?? s.timestamp)}</TableCell>
                          <TableCell><Badge variant="outline" className="text-[9px] font-mono">{s.strategyName || s.strategy || s.signal_kind || "—"}</Badge></TableCell>
                          <TableCell className="font-mono text-xs">{s.symbol}</TableCell>
                          <TableCell><Badge variant={isBuy ? "default" : "destructive"} className="text-[9px] font-bold w-12 justify-center">{s.direction}</Badge></TableCell>
                          <TableCell className="font-mono text-xs text-right font-bold">{s.entry_price?.toFixed?.(2) ?? "—"}</TableCell>
                          <TableCell className="font-mono text-xs text-right text-destructive/80">{s.stop_loss != null ? s.stop_loss.toFixed(2) : "—"}</TableCell>
                          <TableCell className="font-mono text-xs text-right text-accent/80">{s.take_profit != null ? s.take_profit.toFixed(2) : "—"}</TableCell>
                          <TableCell className="font-mono text-xs text-right">{s.score ?? "—"}{s.strength ? <span className="text-[9px] text-muted-foreground ml-1">{s.strength}</span> : null}</TableCell>
                          <TableCell>
                            {s.telegram_sent === false ? (
                              <Badge variant="outline" className="text-[9px] text-muted-foreground">logged</Badge>
                            ) : (
                              <Badge variant="outline" className="text-[9px] text-accent border-accent/30 bg-accent/10">sent</Badge>
                            )}
                          </TableCell>
                          <TableCell>
                            {s.outcome ? (
                              <Badge variant="outline" title={s.outcome_note || undefined} className={`text-[9px] font-bold ${s.outcome === "WIN" ? "text-accent border-accent/30 bg-accent/10" : s.outcome === "LOSS" ? "text-destructive border-destructive/30 bg-destructive/10" : "text-muted-foreground"}`}>{s.outcome}</Badge>
                            ) : (
                              <span className="text-[10px] text-muted-foreground animate-pulse">● open</span>
                            )}
                          </TableCell>
                          <TableCell className="text-right">
                            {(() => {
                              const pnl = signalPnl(s);
                              if (!pnl) return <span className="text-[10px] text-muted-foreground">—</span>;
                              const good = pnl.r >= 0;
                              return (
                                <span className={`font-mono text-xs font-bold ${good ? "text-accent" : "text-destructive"}`}>
                                  {good ? "+" : ""}{pnl.r.toFixed(2)}R
                                  <span className="block text-[9px] font-normal text-muted-foreground">{good ? "+" : ""}{pnl.pips.toFixed(1)} pts</span>
                                </span>
                              );
                            })()}
                          </TableCell>
                        </TableRow>
                        {open && (
                          <TableRow className="hover:bg-transparent">
                            <TableCell colSpan={11} className="p-0">
                              <SignalDetail s={s} />
                            </TableCell>
                          </TableRow>
                        )}
                        </Fragment>
                      );
                    })
                  )}
                </TableBody>
              </Table>
            </div>
          </div>
        )}

      </div>
    </PageLayout>
  );
}
