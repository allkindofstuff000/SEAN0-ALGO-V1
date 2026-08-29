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
  useMarketStatus,
} from "@/hooks/use-trading-data";
import { useState } from "react";
import { Play, BarChart2, History, TrendingUp, Clock } from "lucide-react";
import { useToast } from "@/hooks/use-toast";
import { BacktestResults } from "@/components/BacktestResults";
import { Api, type VwapStBacktestResult } from "@/lib/api";

const TABS = [
  { id: "backtest", label: "Backtest", icon: Play },
  { id: "history", label: "History", icon: History },
];

const isoDaysAgo = (n: number) => {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - n);
  return d.toISOString().split("T")[0];
};

export default function VwapSt() {
  const { toast } = useToast();
  const [activeTab, setActiveTab] = useState("backtest");

  // Config
  const [startDate, setStartDate] = useState(isoDaysAgo(60));
  const [endDate, setEndDate] = useState(isoDaysAgo(1));
  const [balance, setBalance] = useState(5000);
  const [risk, setRisk] = useState([2]);
  const [stPeriod, setStPeriod] = useState([10]);
  const [stMult, setStMult] = useState([3.0]);
  const [slAtr, setSlAtr] = useState([1.5]);
  const [tpAtr, setTpAtr] = useState([3.0]);
  const [maxHold, setMaxHold] = useState([12]);

  const [result, setResult] = useState<VwapStBacktestResult | null>(null);
  const [ranBalance, setRanBalance] = useState(5000);
  const [loadingReport, setLoadingReport] = useState<string | null>(null);

  const { data: live } = useLivePrice();
  const { data: history } = useBacktestHistory();
  const { data: market } = useMarketStatus();
  const runBacktest = useRunVwapStBacktest();

  const vwapReports = (history?.reports || []).filter(
    (r) => r.params?.strategy === "vwap-st" && r.metrics?.total_trades != null,
  );

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

  const handleRunTest = () => {
    const bal = balance;
    runBacktest.mutate(
      {
        start_date: startDate || null,
        end_date: endDate || null,
        starting_balance: bal,
        risk_per_trade_pct: risk[0],
        st_period: Math.round(stPeriod[0]),
        st_mult: stMult[0],
        sl_atr: slAtr[0],
        tp_atr: tpAtr[0],
        max_hold_bars: Math.round(maxHold[0]),
      },
      {
        onSuccess: (res) => {
          if (res.error) {
            toast({
              title: "Backtest error",
              description: res.error,
              variant: "destructive",
            });
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
        onError: (e: any) =>
          toast({
            title: "Backtest failed",
            description: String(e.message || e),
            variant: "destructive",
          }),
      },
    );
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

      {/* Market Pill */}
      <div className="px-4 py-2 border-b border-border/30 shrink-0">
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
      </div>

      {/* Header Card */}
      <div className="mx-4 mt-3 rounded-lg border border-border bg-card px-5 py-3 flex items-center justify-between shrink-0">
        <div>
          <div className="flex items-center gap-2">
            <TrendingUp className="w-4 h-4 text-primary" />
            <span className="font-bold text-lg tracking-tight">XAU/USD</span>
            <Badge className="bg-primary/20 text-primary border-primary/40 text-[10px] font-bold px-2">
              VWAP + SUPERTREND
            </Badge>
            <Badge className="bg-secondary text-muted-foreground border-border text-[10px] font-bold px-2">
              BACKTEST-ONLY
            </Badge>
          </div>
          <p className="text-xs text-muted-foreground mt-0.5">
            Supertrend flip + daily-VWAP confirm · M5 · session 12–21 UTC
          </p>
        </div>
        <div className="text-right">
          <div className="font-mono text-2xl font-bold tracking-tight">
            {price ? price.toFixed(2) : "—"}
          </div>
          <div className="text-xs text-muted-foreground font-mono">live price</div>
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
              </div>

              <Button
                className="w-full mt-4 font-bold uppercase tracking-wider"
                onClick={handleRunTest}
                disabled={runBacktest.isPending}
              >
                {runBacktest.isPending ? (
                  "Running backtest… (fetching OANDA history)"
                ) : (
                  <>
                    <Play className="w-4 h-4 mr-2" />
                    Run Backtest
                  </>
                )}
              </Button>
            </div>

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
            ) : (
              <div className="rounded-lg border border-border border-dashed bg-card/50 p-12 text-center">
                <BarChart2 className="w-10 h-10 text-muted-foreground mx-auto mb-3" />
                <p className="font-bold text-sm uppercase tracking-wider">No backtest yet</p>
                <p className="text-xs text-muted-foreground mt-1">
                  Defaults reflect the walk-forward validated config (ST 10, mult 3.0, 1:2 RR, 2% risk).
                  Adjust and click Run Backtest.
                </p>
              </div>
            )}
          </div>
        )}

        {/* HISTORY TAB */}
        {activeTab === "history" && (
          <div className="h-full overflow-auto pb-4">
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
              <div className="overflow-auto max-h-[560px]">
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
                          No VWAP+ST backtests yet. Run one from the Backtest tab — results are saved automatically.
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
                              {(r.saved_at || "").slice(0, 16).replace("T", " ")}
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
      </div>
    </PageLayout>
  );
}
