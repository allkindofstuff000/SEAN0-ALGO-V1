import { PageLayout } from "@/components/layout/PageLayout";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  useLivePrice,
  useRunVwapStBacktest,
  useBacktestHistory,
  useLiveSignals,
  useMarketStatus,
  useBotStatus,
  useBotControl,
} from "@/hooks/use-trading-data";
import { Fragment, useState } from "react";
import { Play, Square, BarChart2, TrendingUp, Clock, Send } from "lucide-react";
import { useToast } from "@/hooks/use-toast";
import { BacktestResults } from "@/components/BacktestResults";
import { WalkForwardResults } from "@/components/WalkForwardResults";
import { SignalDetail } from "@/components/SignalDetail";
import { SessionPill } from "@/components/SessionPill";
import { fmtLocal, TZ_LABEL } from "@/lib/tz";
import { splitWindow } from "@/lib/walkforward";
import { Api, type VwapStBacktestResult } from "@/lib/api";

const TABS = [
  { id: "backtest", label: "Backtest", icon: Play },
  { id: "signals", label: "Signal History", icon: Send },
];

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

const isoDaysAgo = (n: number) => {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - n);
  return d.toISOString().split("T")[0];
};

export default function VwapSt() {
  const { toast } = useToast();
  const [activeTab, setActiveTab] = useState("backtest");
  const [expandedSig, setExpandedSig] = useState<string | null>(null);

  // Config
  const [startDate, setStartDate] = useState(isoDaysAgo(90));
  const [endDate, setEndDate] = useState(isoDaysAgo(1));
  const [balance, setBalance] = useState(5000);
  const [risk, setRisk] = useState([2]);
  const [stPeriod, setStPeriod] = useState([10]);
  const [stMult, setStMult] = useState([3.0]);
  const [slAtr, setSlAtr] = useState([1.5]);
  const [tpAtr, setTpAtr] = useState([3.0]);
  const [maxHold, setMaxHold] = useState([12]);
  const [lag, setLag] = useState([0]); // detection lag (seconds): 0 / 60 / 120 — honest M1-drift fill

  const [result, setResult] = useState<VwapStBacktestResult | null>(null);
  const [ranBalance, setRanBalance] = useState(5000);
  const [loadingReport, setLoadingReport] = useState<string | null>(null);
  const [wf, setWf] = useState<{ train: VwapStBacktestResult; test: VwapStBacktestResult; split: string } | null>(null);
  const [wfRunning, setWfRunning] = useState(false);

  const { data: live } = useLivePrice();
  const { data: history } = useBacktestHistory();
  const { data: signalsData } = useLiveSignals(100);
  const { data: market } = useMarketStatus();
  const { data: botStatus } = useBotStatus();
  const runBacktest = useRunVwapStBacktest();
  const botControl = useBotControl();
  const vwapRunning = !!botStatus?.vwapSt?.running;
  const toggleVwapBot = () => {
    botControl.mutate(
      { strategy: "vwap-st", action: vwapRunning ? "STOP" : "START" },
      {
        onSuccess: (r: any) => toast({ title: `VWAP+ST ${vwapRunning ? "Stopped" : "Started"}`, description: r?.message || "" }),
        onError: (e: any) => toast({ title: "Bot control failed", description: String(e?.message || e), variant: "destructive" }),
      },
    );
  };

  const vwapReports = (history?.reports || []).filter(
    (r) => r.params?.strategy === "vwap-st" && r.metrics?.total_trades != null,
  );

  // VWAP+ST live signals only (this page is strategy-specific)
  const vwapSignals = (signalsData?.signals || []).filter((s) => {
    const blob = `${s.strategy || ""} ${s.strategyName || ""} ${s.signal_kind || ""}`.toLowerCase();
    return blob.includes("vwap") || blob.includes("supertrend");
  });

  // Win/loss summary over resolved signals
  const sigResolved = vwapSignals.filter((s) => s.outcome === "WIN" || s.outcome === "LOSS");
  const sigWins = sigResolved.filter((s) => s.outcome === "WIN").length;
  const sigLosses = sigResolved.filter((s) => s.outcome === "LOSS").length;
  const sigWinRate = sigResolved.length ? (sigWins / sigResolved.length) * 100 : 0;
  const sigOpen = vwapSignals.length - sigResolved.length;
  const sigNetR = sigResolved.reduce((acc, s) => acc + (signalPnl(s)?.r ?? 0), 0);

  const openReport = async (id: string) => {
    setLoadingReport(id);
    try {
      const doc = await Api.backtestReport(id);
      setResult({
        metrics: doc.metrics as any,
        trades: doc.trades || [],
        equity_curve: doc.equity_curve || [],
      });
      setRanBalance(Number(doc.params?.starting_balance) || 5000);
      setActiveTab("backtest");
    } catch (e: any) {
      toast({
        title: "Could not load report",
        description: String(e.message || e),
        variant: "destructive",
      });
    } finally {
      setLoadingReport(null);
    }
  };

  const vwapParams = (start: string, end: string) => ({
    start_date: start || null,
    end_date: end || null,
    starting_balance: balance,
    risk_per_trade_pct: risk[0],
    st_period: Math.round(stPeriod[0]),
    st_mult: stMult[0],
    sl_atr: slAtr[0],
    tp_atr: tpAtr[0],
    max_hold_bars: Math.round(maxHold[0]),
    detection_lag_seconds: lag[0] || 0,
  });

  const handleRunTest = () => {
    const bal = balance;
    setWf(null);
    runBacktest.mutate(vwapParams(startDate, endDate), {
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
          description: `${m.total_trades} trades · ${m.win_rate.toFixed(1)}% win · PF ${m.profit_factor.toFixed(2)} · final $${m.ending_balance.toFixed(2)}`,
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
      const train = await Api.runVwapStBacktest(vwapParams(startDate, split));
      if (train.error) throw new Error(train.error);
      const test = await Api.runVwapStBacktest(vwapParams(split, endDate));
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
  const price = live?.price ?? 0;

  return (
    <PageLayout>
      <div className="flex items-center border-b border-border/60 bg-card/50 px-4 shrink-0">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => setActiveTab(id)}
            className={`flex items-center gap-1.5 px-4 py-3 text-xs font-bold uppercase tracking-wider border-b-2 transition-colors ${
              activeTab === id
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            <Icon className="w-3.5 h-3.5" />
            {label}
          </button>
        ))}
      </div>

      {/* Market + Session Pills */}
      <div className="px-4 py-2 border-b border-border/30 shrink-0 flex items-center gap-2 flex-wrap">
        <div
          className={`inline-flex items-center gap-2 px-3 py-1 rounded-full border text-xs font-bold ${
            marketClosed
              ? "bg-secondary/60 border-border text-muted-foreground"
              : "bg-accent/10 border-accent/20 text-accent"
          }`}
        >
          <span
            className={`w-1.5 h-1.5 rounded-full ${
              marketClosed ? "bg-yellow-500/80" : "bg-accent animate-pulse"
            }`}
          />
          XAUUSD · Forex Market ·{" "}
          <span className={marketClosed ? "text-yellow-500" : "text-accent"}>
            {marketClosed ? market?.reason || "Closed" : "Open"}
          </span>
        </div>
        <SessionPill />
      </div>

      {/* Header Card */}
      <div className="mx-4 mt-3 rounded-lg border border-border bg-card px-5 py-3 flex items-center justify-between shrink-0">
        <div>
          <div className="flex items-center gap-2">
            <TrendingUp className="w-4 h-4 text-primary" />
            <span className="font-bold text-lg tracking-tight">XAU/USD</span>
            {vwapRunning && <Badge className="bg-accent/20 text-accent border-accent/40 text-[10px] font-bold px-2 animate-pulse">LIVE</Badge>}
            <Badge className="bg-primary/20 text-primary border-primary/40 text-[10px] font-bold px-2">
              VWAP + SUPERTREND
            </Badge>
          </div>
          <p className="text-xs text-muted-foreground mt-0.5">
            Supertrend flip + daily-VWAP confirm · M5 · session 12–21 UTC
          </p>
        </div>
        <div className="flex items-center gap-6">
          <Button onClick={toggleVwapBot} disabled={botControl.isPending} variant={vwapRunning ? "destructive" : "default"} className="font-bold uppercase tracking-wider h-9">
            {vwapRunning ? <><Square className="w-3.5 h-3.5 mr-1.5 fill-current" />Stop</> : <><Play className="w-3.5 h-3.5 mr-1.5 fill-current" />Start</>}
          </Button>
          <div className="text-right">
            <div className="font-mono text-2xl font-bold tracking-tight">
              {price ? price.toFixed(2) : "—"}
            </div>
            <div className="text-xs text-muted-foreground font-mono">live price</div>
          </div>
        </div>
      </div>

      <div className="flex-1 mx-4 mt-2 mb-4 min-h-0 overflow-hidden">
        {/* BACKTEST TAB */}
        {activeTab === "backtest" && (
          <div className="h-full overflow-auto pb-4 space-y-4">
            {/* Config bar */}
            <div className="rounded-lg border border-border bg-card p-4">
              <div className="flex items-center justify-between mb-3">
                <p className="text-xs font-bold uppercase tracking-wider">
                  Backtest Configuration
                </p>
                <p className="text-[10px] font-mono text-muted-foreground">
                  XAUUSD M5 · live OANDA history · Supertrend + session VWAP
                </p>
              </div>

              {/* Row 1 — window + balance */}
              <div className="grid grid-cols-2 md:grid-cols-3 gap-4 items-end mb-4">
                <div className="space-y-1.5">
                  <label className="text-[10px] font-bold text-muted-foreground uppercase">
                    Start Date
                  </label>
                  <Input
                    type="date"
                    value={startDate}
                    onChange={(e) => setStartDate(e.target.value)}
                    className="h-8 text-xs font-mono bg-background"
                  />
                </div>
                <div className="space-y-1.5">
                  <label className="text-[10px] font-bold text-muted-foreground uppercase">
                    End Date
                  </label>
                  <Input
                    type="date"
                    value={endDate}
                    onChange={(e) => setEndDate(e.target.value)}
                    className="h-8 text-xs font-mono bg-background"
                  />
                </div>
                <div className="space-y-1.5">
                  <label className="text-[10px] font-bold text-muted-foreground uppercase">
                    Balance (USD)
                  </label>
                  <Input
                    type="number"
                    value={balance}
                    onChange={(e) => setBalance(Number(e.target.value))}
                    className="h-8 text-xs font-mono bg-background"
                  />
                </div>
              </div>

              {/* Row 2 — sliders */}
              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4 items-end">
                <div className="space-y-2">
                  <div className="flex justify-between">
                    <label className="text-[10px] font-bold text-muted-foreground uppercase">
                      Risk %
                    </label>
                    <span className="text-xs font-mono text-destructive font-bold">
                      {risk[0]}%
                    </span>
                  </div>
                  <Slider value={risk} onValueChange={setRisk} min={0.5} max={10} step={0.5} />
                </div>
                <div className="space-y-2">
                  <div className="flex justify-between">
                    <label className="text-[10px] font-bold text-muted-foreground uppercase">
                      ST Period
                    </label>
                    <span className="text-xs font-mono text-primary font-bold">
                      {stPeriod[0]}
                    </span>
                  </div>
                  <Slider value={stPeriod} onValueChange={setStPeriod} min={7} max={21} step={1} />
                </div>
                <div className="space-y-2">
                  <div className="flex justify-between">
                    <label className="text-[10px] font-bold text-muted-foreground uppercase">
                      ST Mult
                    </label>
                    <span className="text-xs font-mono text-primary font-bold">
                      {stMult[0].toFixed(1)}
                    </span>
                  </div>
                  <Slider value={stMult} onValueChange={setStMult} min={1.5} max={5} step={0.1} />
                </div>
                <div className="space-y-2">
                  <div className="flex justify-between">
                    <label className="text-[10px] font-bold text-muted-foreground uppercase">
                      SL ATR
                    </label>
                    <span className="text-xs font-mono text-primary font-bold">
                      {slAtr[0].toFixed(1)}x
                    </span>
                  </div>
                  <Slider value={slAtr} onValueChange={setSlAtr} min={0.5} max={5} step={0.1} />
                </div>
                <div className="space-y-2">
                  <div className="flex justify-between">
                    <label className="text-[10px] font-bold text-muted-foreground uppercase">
                      TP ATR
                    </label>
                    <span className="text-xs font-mono text-primary font-bold">
                      {tpAtr[0].toFixed(1)}x
                    </span>
                  </div>
                  <Slider value={tpAtr} onValueChange={setTpAtr} min={1} max={10} step={0.5} />
                </div>
                <div className="space-y-2">
                  <div className="flex justify-between">
                    <label className="text-[10px] font-bold text-muted-foreground uppercase">
                      Max Hold
                    </label>
                    <span className="text-xs font-mono text-primary font-bold">
                      {maxHold[0]} bars
                    </span>
                  </div>
                  <Slider value={maxHold} onValueChange={setMaxHold} min={3} max={48} step={1} />
                </div>
                <div className="space-y-2">
                  <div className="flex justify-between">
                    <label className="text-[10px] font-bold text-muted-foreground uppercase" title="Honest detection-lag: fills at the REAL M1 price this many seconds after the signal bar closes (models the ~60s live poll — can help or hurt). 0 = exact next-bar open. Sub-60s rounds down (M1 granularity).">
                      Poll lag
                    </label>
                    <span className="text-xs font-mono text-yellow-500 font-bold">{lag[0] === 0 ? "off" : `${lag[0]}s`}</span>
                  </div>
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
                <p className="text-xs text-muted-foreground">
                  Running VWAP+ST strategy over OANDA history…
                </p>
              </div>
            ) : result ? (
              <BacktestResults result={result} startBalance={ranBalance} />
            ) : !wf ? (
              <div className="rounded-lg border border-border border-dashed bg-card/50 p-12 text-center">
                <BarChart2 className="w-10 h-10 text-muted-foreground mx-auto mb-3" />
                <p className="font-bold text-sm uppercase tracking-wider">No backtest yet</p>
                <p className="text-xs text-muted-foreground mt-1">
                  Defaults reflect the walk-forward validated config (ST 10, mult 3.0, 1:2 RR, 2% risk).
                  Adjust and click Run Backtest.
                </p>
              </div>
            ) : null}

            {/* Backtest history — nested inside the Backtest tab */}
            <div className="rounded-lg border border-border bg-card overflow-hidden">
              <div className="px-4 py-3 border-b border-border bg-secondary/20 flex items-center gap-2">
                <Clock className="w-3.5 h-3.5 text-muted-foreground" />
                <p className="text-xs font-bold uppercase tracking-wider">
                  VWAP+ST Backtest History
                </p>
                <p className="text-[10px] text-muted-foreground font-mono ml-1">
                  click a run to reopen its full report
                </p>
              </div>
              <div className="overflow-auto max-h-[360px]">
                <Table>
                  <TableHeader className="sticky top-0 bg-card z-10">
                    <TableRow className="hover:bg-transparent border-border">
                      {[
                        "Run Date",
                        "Window",
                        "Trades",
                        "Win %",
                        "PF",
                        "Max DD (R)",
                        "Final $",
                        "",
                      ].map((h) => (
                        <TableHead
                          key={h}
                          className={`font-mono text-[10px] uppercase text-muted-foreground ${
                            ["Trades", "Win %", "PF", "Max DD (R)", "Final $"].includes(h)
                              ? "text-right"
                              : ""
                          }`}
                        >
                          {h}
                        </TableHead>
                      ))}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {vwapReports.length === 0 ? (
                      <TableRow>
                        <TableCell
                          colSpan={8}
                          className="text-center py-8 text-xs text-muted-foreground"
                        >
                          No VWAP+ST backtests yet. Run one above — results are saved automatically.
                        </TableCell>
                      </TableRow>
                    ) : (
                      vwapReports.map((r) => {
                        const m = r.metrics;
                        const start = Number(r.params?.starting_balance) || 0;
                        const profit = (m.ending_balance ?? 0) - start;
                        return (
                          <TableRow
                            key={r._id}
                            className="border-border/50 hover:bg-secondary/20 cursor-pointer"
                            onClick={() => openReport(r._id)}
                          >
                            <TableCell className="font-mono text-xs text-muted-foreground">
                              {fmtLocal(r.saved_at)}
                            </TableCell>
                            <TableCell className="font-mono text-[11px] text-muted-foreground">
                              {r.params?.start_date || "?"} → {r.params?.end_date || "?"}
                            </TableCell>
                            <TableCell className="font-mono text-xs text-right">
                              {m.total_trades ?? r.trade_count ?? 0}
                            </TableCell>
                            <TableCell className="font-mono text-xs text-right font-bold text-primary">
                              {(m.win_rate ?? 0).toFixed(1)}%
                            </TableCell>
                            <TableCell className="font-mono text-xs text-right">
                              {Number.isFinite(m.profit_factor)
                                ? (m.profit_factor ?? 0).toFixed(2)
                                : "∞"}
                            </TableCell>
                            <TableCell className="font-mono text-xs text-right text-destructive">
                              {(m.max_drawdown_r ?? 0).toFixed(2)}
                            </TableCell>
                            <TableCell
                              className={`font-mono text-xs text-right font-bold ${
                                profit >= 0 ? "text-accent" : "text-destructive"
                              }`}
                            >
                              ${(m.ending_balance ?? 0).toFixed(0)}
                            </TableCell>
                            <TableCell className="text-right">
                              <span className="text-[10px] text-primary font-bold uppercase">
                                {loadingReport === r._id ? "Loading…" : "View →"}
                              </span>
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

        {/* SIGNAL HISTORY TAB — VWAP+ST fired signals */}
        {activeTab === "signals" && (
          <div className="h-full overflow-auto pb-4">
            <div className="rounded-lg border border-border bg-card overflow-hidden">
              <div className="px-4 py-3 border-b border-border bg-secondary/20 flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <Send className="w-3.5 h-3.5 text-muted-foreground" />
                  <p className="text-xs font-bold uppercase tracking-wider">Signal History</p>
                  <p className="text-[10px] text-muted-foreground font-mono ml-1 hidden md:inline">every VWAP+ST signal · auto-marked WIN/LOSS when price hits TP or SL</p>
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
                      {[`Time (${TZ_LABEL})`, "Strategy", "Symbol", "Dir", "Entry", "SL", "TP", "ATR", "TG", "Outcome", "P&L"].map((h) => (
                        <TableHead key={h} className={`font-mono text-[10px] uppercase text-muted-foreground ${["Entry", "SL", "TP", "ATR", "P&L"].includes(h) ? "text-right" : ""}`}>{h}</TableHead>
                      ))}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {vwapSignals.length === 0 ? (
                      <TableRow><TableCell colSpan={11} className="text-center py-10 text-xs text-muted-foreground">No VWAP+ST signals yet. When a Supertrend flip confirms with the daily VWAP during session (12–21 UTC), the signal appears here and is sent to Telegram.</TableCell></TableRow>
                    ) : (
                      vwapSignals.map((s) => {
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
                            <TableCell className="font-mono text-xs text-right text-primary">{s.atr != null ? s.atr.toFixed(2) : "—"}</TableCell>
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
          </div>
        )}
      </div>
    </PageLayout>
  );
}
