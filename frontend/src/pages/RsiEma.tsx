import { PageLayout } from "@/components/layout/PageLayout";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useCandles, useXauStatus, useRunBacktest, useXauSignals, useMarketStatus } from "@/hooks/use-trading-data";
import { useMemo, useState } from "react";
import { Play, BarChart2, History, SlidersHorizontal, TrendingUp } from "lucide-react";
import { useToast } from "@/hooks/use-toast";
import { LiveChart } from "@/components/LiveChart";
import { computeIndicators } from "@/lib/indicators";
import type { BacktestMetrics } from "@/lib/api";

const TABS = [
  { id: "chart", label: "Chart", icon: BarChart2 },
  { id: "backtest", label: "Backtest", icon: Play },
  { id: "signals", label: "Signal History", icon: History },
  { id: "optimizer", label: "Optimizer", icon: SlidersHorizontal },
];

const TF_MAP: Record<string, string> = { "1M": "M1", "5M": "M5", "15M": "M15", "1H": "H1" };

type RunRow = { id: string; date: string; metrics: BacktestMetrics; params: string };

export default function RsiEma() {
  const { toast } = useToast();
  const [activeTab, setActiveTab] = useState("chart");
  const [activeTF, setActiveTF] = useState("5M");
  const [showEma, setShowEma] = useState(true);
  const [sl, setSl] = useState([1.5]);
  const [tp, setTp] = useState([3.0]);
  const [risk, setRisk] = useState([2]);
  const [startDate, setStartDate] = useState("2025-04-01");
  const [endDate, setEndDate] = useState("2025-05-01");
  const [balance, setBalance] = useState(10000);
  const [runs, setRuns] = useState<RunRow[]>([]);

  const tf = TF_MAP[activeTF];
  const { data: candleData } = useCandles(tf, 240);
  const { data: status } = useXauStatus();
  const { data: signalsData } = useXauSignals(20);
  const { data: market } = useMarketStatus();
  const runBacktest = useRunBacktest();

  const ind = useMemo(() => computeIndicators(candleData?.candles ?? []), [candleData]);
  const livePrice = status?.livePrice?.price || ind?.price || 0;
  const changeAbs = ind?.changeAbs ?? 0;
  const changePct = ind?.changePct ?? 0;

  const stats = [
    { label: "RSI (14)", val: ind ? ind.rsi.toFixed(1) : "—", color: ind && ind.rsi > 70 ? "text-destructive" : ind && ind.rsi < 30 ? "text-accent" : "text-accent" },
    { label: "EMA 9", val: ind ? ind.ema9.toFixed(2) : "—", color: "text-foreground" },
    { label: "EMA 21", val: ind ? ind.ema21.toFixed(2) : "—", color: "text-foreground" },
    { label: "ATR", val: ind ? ind.atr.toFixed(2) : "—", color: "text-primary" },
  ];

  const handleRunTest = () => {
    runBacktest.mutate(
      {
        start_date: startDate || null,
        end_date: endDate || null,
        starting_balance: balance,
        risk_per_trade_pct: risk[0],
        max_hold_bars: 60,
      },
      {
        onSuccess: (res) => {
          if (res.error) {
            toast({ title: "Backtest error", description: res.error, variant: "destructive" });
            return;
          }
          const m = res.metrics || {};
          setRuns((prev) => [
            {
              id: `#${prev.length + 1}`,
              date: new Date().toISOString().split("T")[0],
              metrics: m,
              params: `Risk ${risk[0]}% · SL ${sl[0]} / TP ${tp[0]} ATR · $${balance}`,
            },
            ...prev,
          ]);
          toast({
            title: "Backtest Complete",
            description: `${m.totalTrades ?? 0} trades · ${(m.winRate ?? 0).toFixed?.(1) ?? m.winRate}% win · final $${(m.finalBalance ?? 0).toFixed?.(2) ?? m.finalBalance}`,
          });
        },
        onError: (e: any) => toast({ title: "Backtest failed", description: String(e.message || e), variant: "destructive" }),
      },
    );
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

      {/* Market Pill */}
      <div className="px-4 py-2 border-b border-border/30 shrink-0">
        <div className={`inline-flex items-center gap-2 px-3 py-1 rounded-full border text-xs font-bold ${marketClosed ? "bg-secondary/60 border-border text-muted-foreground" : "bg-accent/10 border-accent/20 text-accent"}`}>
          <span className={`w-1.5 h-1.5 rounded-full ${marketClosed ? "bg-yellow-500/80" : "bg-accent animate-pulse"}`} />
          XAUUSD · Forex Market · <span className={marketClosed ? "text-yellow-500" : "text-accent"}>{marketClosed ? (market?.reason || "Closed") : "Open"}</span>
        </div>
      </div>

      {/* Header Card */}
      <div className="mx-4 mt-3 rounded-lg border border-border bg-card px-5 py-3 flex items-center justify-between shrink-0">
        <div>
          <div className="flex items-center gap-2">
            <TrendingUp className="w-4 h-4 text-primary" />
            <span className="font-bold text-lg tracking-tight">XAU/USD</span>
            <Badge className="bg-primary/20 text-primary border-primary/40 text-[10px] font-bold px-2">RSI EMA</Badge>
          </div>
          <p className="text-xs text-muted-foreground mt-0.5">Multi-timeframe RSI + EMA crossover · {activeTF}</p>
        </div>
        <div className="text-right">
          <div className="font-mono text-2xl font-bold tracking-tight">{livePrice ? livePrice.toFixed(2) : "—"}</div>
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
          <div className="h-full overflow-auto">
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 pb-4">
              <div className="rounded-lg border border-border bg-card overflow-hidden">
                <div className="px-4 py-3 border-b border-border bg-secondary/20">
                  <p className="text-xs font-bold uppercase tracking-wider">Configuration</p>
                  <p className="text-[10px] font-mono text-muted-foreground mt-0.5">XAUUSD · live OANDA history</p>
                </div>
                <div className="p-4 space-y-5">
                  <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-1.5">
                      <label className="text-[10px] font-bold text-muted-foreground uppercase">Start Date</label>
                      <Input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} className="h-8 text-xs font-mono bg-background" />
                    </div>
                    <div className="space-y-1.5">
                      <label className="text-[10px] font-bold text-muted-foreground uppercase">End Date</label>
                      <Input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} className="h-8 text-xs font-mono bg-background" />
                    </div>
                  </div>
                  <div className="space-y-2">
                    <div className="flex justify-between">
                      <label className="text-[10px] font-bold text-muted-foreground uppercase">Stop-Loss ATR</label>
                      <span className="text-xs font-mono text-primary font-bold">{sl[0].toFixed(1)}x</span>
                    </div>
                    <Slider value={sl} onValueChange={setSl} min={0.5} max={5} step={0.1} />
                  </div>
                  <div className="space-y-2">
                    <div className="flex justify-between">
                      <label className="text-[10px] font-bold text-muted-foreground uppercase">Take-Profit ATR</label>
                      <span className="text-xs font-mono text-primary font-bold">{tp[0].toFixed(1)}x</span>
                    </div>
                    <Slider value={tp} onValueChange={setTp} min={1} max={10} step={0.5} />
                  </div>
                  <div className="space-y-1.5">
                    <label className="text-[10px] font-bold text-muted-foreground uppercase">Starting Balance (USD)</label>
                    <Input type="number" value={balance} onChange={(e) => setBalance(Number(e.target.value))} className="h-8 text-xs font-mono bg-background" />
                  </div>
                  <div className="space-y-2">
                    <div className="flex justify-between">
                      <label className="text-[10px] font-bold text-muted-foreground uppercase">Risk Per Trade</label>
                      <span className="text-xs font-mono text-destructive font-bold">{risk[0]}%</span>
                    </div>
                    <Slider value={risk} onValueChange={setRisk} min={0.5} max={5} step={0.5} />
                  </div>
                  <Button className="w-full font-bold uppercase tracking-wider" onClick={handleRunTest} disabled={runBacktest.isPending}>
                    {runBacktest.isPending ? "Running…" : <><Play className="w-4 h-4 mr-2" />Run Backtest</>}
                  </Button>
                </div>
              </div>

              <div className="col-span-1 lg:col-span-2 rounded-lg border border-border bg-card overflow-hidden flex flex-col">
                <div className="px-4 py-3 border-b border-border bg-secondary/20 shrink-0">
                  <p className="text-xs font-bold uppercase tracking-wider">Backtest Results</p>
                </div>
                <div className="overflow-auto flex-1">
                  <Table>
                    <TableHeader className="sticky top-0 bg-card border-b border-border z-10">
                      <TableRow className="hover:bg-transparent">
                        <TableHead className="font-mono text-[10px] uppercase text-muted-foreground w-[60px]">ID</TableHead>
                        <TableHead className="font-mono text-[10px] uppercase text-muted-foreground">Date</TableHead>
                        <TableHead className="font-mono text-[10px] uppercase text-muted-foreground text-right">Trades</TableHead>
                        <TableHead className="font-mono text-[10px] uppercase text-muted-foreground text-right">Win Rate</TableHead>
                        <TableHead className="font-mono text-[10px] uppercase text-muted-foreground text-right">PF</TableHead>
                        <TableHead className="font-mono text-[10px] uppercase text-muted-foreground text-right">Final $</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {runs.length === 0 ? (
                        <TableRow><TableCell colSpan={6} className="text-center py-8 text-xs text-muted-foreground">Run a backtest to see results.</TableCell></TableRow>
                      ) : (
                        runs.map((bt) => (
                          <TableRow key={bt.id} className="border-b border-border/50 hover:bg-secondary/20">
                            <TableCell className="font-mono text-xs font-bold">{bt.id}</TableCell>
                            <TableCell className="font-mono text-xs text-muted-foreground">{bt.date}</TableCell>
                            <TableCell className="font-mono text-xs text-right">{bt.metrics.totalTrades ?? 0}</TableCell>
                            <TableCell className="font-mono text-xs text-right font-bold text-primary">{(bt.metrics.winRate ?? 0).toFixed?.(1) ?? bt.metrics.winRate}%</TableCell>
                            <TableCell className="font-mono text-xs text-right">{(bt.metrics.profitFactor ?? 0).toFixed?.(2) ?? bt.metrics.profitFactor}</TableCell>
                            <TableCell className={`font-mono text-xs text-right font-bold ${(bt.metrics.finalBalance ?? 0) >= balance ? "text-accent" : "text-destructive"}`}>
                              ${(bt.metrics.finalBalance ?? 0).toFixed?.(2) ?? bt.metrics.finalBalance}
                            </TableCell>
                          </TableRow>
                        ))
                      )}
                    </TableBody>
                  </Table>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* SIGNALS TAB */}
        {activeTab === "signals" && (
          <div className="rounded-lg border border-border bg-card overflow-hidden">
            <div className="px-4 py-3 border-b border-border bg-secondary/20">
              <p className="text-xs font-bold uppercase tracking-wider">Recent Signals</p>
            </div>
            <Table>
              <TableHeader>
                <TableRow className="border-border hover:bg-transparent bg-secondary/30">
                  <TableHead className="font-mono text-[10px] uppercase text-muted-foreground w-[160px]">Time</TableHead>
                  <TableHead className="font-mono text-[10px] uppercase text-muted-foreground">Type</TableHead>
                  <TableHead className="font-mono text-[10px] uppercase text-muted-foreground text-right">Price</TableHead>
                  <TableHead className="font-mono text-[10px] uppercase text-muted-foreground">Context</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(signalsData?.signals?.length ?? 0) === 0 ? (
                  <TableRow><TableCell colSpan={4} className="text-center py-8 text-xs text-muted-foreground">No signals yet — waiting for setup.</TableCell></TableRow>
                ) : (
                  signalsData!.signals.map((sig, i) => (
                    <TableRow key={i} className="border-border hover:bg-secondary/20">
                      <TableCell className="font-mono text-xs text-muted-foreground">{sig.time || (sig.ts ? new Date(sig.ts).toLocaleTimeString() : "—")}</TableCell>
                      <TableCell>
                        <Badge variant={(sig.direction || "").toUpperCase() === "BUY" ? "default" : "destructive"} className="text-[10px] font-bold w-16 justify-center">
                          {sig.direction || "—"}
                        </Badge>
                      </TableCell>
                      <TableCell className="font-mono text-xs text-right font-bold">{sig.price?.toFixed?.(2) ?? "—"}</TableCell>
                      <TableCell className="text-xs text-muted-foreground">{sig.reason || sig.strength || "—"}</TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>
        )}

        {/* OPTIMIZER TAB */}
        {activeTab === "optimizer" && (
          <div className="rounded-lg border border-border bg-card p-6 text-center">
            <SlidersHorizontal className="w-10 h-10 text-muted-foreground mx-auto mb-3" />
            <p className="font-bold text-sm uppercase tracking-wider text-foreground">Parameter Optimizer</p>
            <p className="text-xs text-muted-foreground mt-1">Grid search and walk-forward optimization coming soon</p>
          </div>
        )}
      </div>
    </PageLayout>
  );
}
