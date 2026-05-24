import { PageLayout } from "@/components/layout/PageLayout";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useBacktests, useSignals, useRunBacktest } from "@/hooks/use-trading-data";
import { useState } from "react";
import { Play, BarChart2, History, SlidersHorizontal, TrendingUp } from "lucide-react";
import { useToast } from "@/hooks/use-toast";

const TABS = [
  { id: "chart", label: "Chart", icon: BarChart2 },
  { id: "backtest", label: "Backtest", icon: Play },
  { id: "signals", label: "Signal History", icon: History },
  { id: "optimizer", label: "Optimizer", icon: SlidersHorizontal },
];

const CANDLE_DATA = [
  { x: 12, h: 65, body: 45, up: true },
  { x: 18, h: 50, body: 35, up: false },
  { x: 24, h: 80, body: 58, up: true },
  { x: 30, h: 42, body: 28, up: true },
  { x: 36, h: 70, body: 52, up: false },
  { x: 42, h: 55, body: 38, up: true },
  { x: 48, h: 90, body: 65, up: true },
  { x: 54, h: 48, body: 32, up: false },
  { x: 60, h: 75, body: 55, up: true },
];

export default function RsiEma() {
  const { data: backtests, isLoading: isTestsLoading } = useBacktests();
  const { data: signals, isLoading: isSignalsLoading } = useSignals();
  const runTest = useRunBacktest();
  const { toast } = useToast();
  const [activeTab, setActiveTab] = useState("backtest");
  const [activeTF, setActiveTF] = useState("5M");
  const [sl, setSl] = useState([1.5]);
  const [tp, setTp] = useState([3.0]);
  const [risk, setRisk] = useState([2]);

  const handleRunTest = () => {
    runTest.mutate({ sl: sl[0], tp: tp[0], risk: risk[0] }, {
      onSuccess: () => {
        toast({ title: "Backtest Complete", description: "New results added to history." });
      },
    });
  };

  return (
    <PageLayout>
      {/* Sub-tabs */}
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
        <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-secondary/60 border border-border text-xs font-bold text-muted-foreground">
          <span className="w-1.5 h-1.5 rounded-full bg-yellow-500/80" />
          XAUUSD · Forex Market · <span className="text-yellow-500">Weekend Closed</span> · Opens 22:00 Sun UTC
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
          <p className="text-xs text-muted-foreground mt-0.5">Multi-timeframe RSI + EMA crossover · 5M / 15M</p>
        </div>
        <div className="text-right">
          <div className="font-mono text-2xl font-bold tracking-tight">1991.24</div>
          <div className="font-mono text-sm text-accent font-bold">+3.20 (0.16%)</div>
        </div>
      </div>

      {/* Stats Row */}
      <div className="mx-4 mt-2 grid grid-cols-4 border border-border rounded-lg overflow-hidden shrink-0">
        {[
          { label: "RSI (14)", val: "58.4", color: "text-accent" },
          { label: "EMA 9", val: "1989.50", color: "text-foreground" },
          { label: "EMA 21", val: "1985.20", color: "text-foreground" },
          { label: "ATR", val: "4.82", color: "text-primary" },
        ].map((s, i) => (
          <div
            key={s.label}
            className={`px-4 py-3 flex flex-col gap-1 bg-card ${i > 0 ? "border-l border-border" : ""}`}
          >
            <span className="text-[10px] text-muted-foreground uppercase font-bold">{s.label}</span>
            <span className={`font-mono text-sm font-bold ${s.color}`}>{s.val}</span>
          </div>
        ))}
      </div>

      {/* Tab Content */}
      <div className="flex-1 mx-4 mt-2 mb-4 min-h-0 overflow-hidden">

        {/* CHART TAB */}
        {activeTab === "chart" && (
          <div className="h-full min-h-[400px] flex flex-col rounded-lg border border-border bg-[#131722] overflow-hidden">
            {/* Chart toolbar */}
            <div className="flex items-center gap-3 px-3 py-2 border-b border-[#2A2E39] bg-[#1E222D] shrink-0">
              <span className="font-bold text-white text-sm">XAUUSD</span>
              <span className="text-xs text-accent font-mono">+0.16%</span>
              <div className="h-3 w-px bg-[#2A2E39]" />
              <div className="flex items-center gap-1">
                {["1M", "5M", "15M", "1H"].map((tf) => (
                  <button
                    key={tf}
                    onClick={() => setActiveTF(tf)}
                    className={`px-2 py-0.5 text-xs font-bold rounded transition-all ${activeTF === tf ? "bg-blue-600 text-white" : "text-gray-400 hover:text-white hover:bg-[#2A2E39]"}`}
                  >
                    {tf}
                  </button>
                ))}
              </div>
              <div className="h-3 w-px bg-[#2A2E39]" />
              <div className="flex items-center gap-2 text-xs">
                {[
                  { label: "EMA 9", color: "text-yellow-400" },
                  { label: "EMA 21", color: "text-blue-400" },
                  { label: "RSI", color: "text-purple-400" },
                  { label: "ATR", color: "text-gray-400" },
                ].map((o) => (
                  <button key={o.label} className={`${o.color} hover:opacity-80 font-medium`}>{o.label}</button>
                ))}
              </div>
            </div>
            <div
              className="flex-1 relative"
              style={{
                backgroundImage: "linear-gradient(#1E222D 1px, transparent 1px), linear-gradient(90deg, #1E222D 1px, transparent 1px)",
                backgroundSize: "60px 50px",
              }}
            >
              <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                <span className="text-[#2A2E39] text-3xl font-bold opacity-30 select-none">XAUUSD RSI EMA MOCKUP</span>
              </div>
              {/* Mock EMA lines */}
              <svg className="absolute inset-0 w-full h-full" preserveAspectRatio="none">
                <polyline points="0,75% 20%,68% 40%,55% 60%,48% 80%,42% 100%,38%" fill="none" stroke="#F7A600" strokeWidth="1.5" strokeOpacity="0.7" />
                <polyline points="0,80% 20%,74% 40%,62% 60%,56% 80%,50% 100%,45%" fill="none" stroke="#3B82F6" strokeWidth="1.5" strokeOpacity="0.7" />
              </svg>
              {/* Candles */}
              {CANDLE_DATA.map((c, i) => (
                <div key={i} className="absolute bottom-[30%]" style={{ left: `${c.x}%` }}>
                  <div className={`w-0.5 mx-auto ${c.up ? "bg-green-500" : "bg-red-500"}`} style={{ height: c.h * 0.8 }} />
                  <div className={`w-2.5 -ml-1 ${c.up ? "bg-green-500" : "bg-red-500"}`} style={{ height: c.body * 0.6 }} />
                </div>
              ))}
              {/* Price axis */}
              <div className="absolute right-0 top-0 bottom-0 w-14 bg-[#131722] border-l border-[#2A2E39] flex flex-col justify-between py-8 font-mono text-[10px] text-gray-500 items-end pr-2 pointer-events-none">
                <span>2005.00</span>
                <span>2000.00</span>
                <span className="text-white bg-[#2A2E39] px-1 rounded -mr-1 py-0.5">1991.24</span>
                <span>1985.00</span>
                <span>1980.00</span>
              </div>
            </div>
          </div>
        )}

        {/* BACKTEST TAB */}
        {activeTab === "backtest" && (
          <div className="h-full overflow-auto">
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 pb-4">
              {/* Config Form */}
              <div className="rounded-lg border border-border bg-card overflow-hidden">
                <div className="px-4 py-3 border-b border-border bg-secondary/20">
                  <p className="text-xs font-bold uppercase tracking-wider">Configuration</p>
                  <p className="text-[10px] font-mono text-muted-foreground mt-0.5">XAUUSD 5M / 15M</p>
                </div>
                <div className="p-4 space-y-5">
                  <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-1.5">
                      <label className="text-[10px] font-bold text-muted-foreground uppercase">Start Date</label>
                      <Input type="date" className="h-8 text-xs font-mono bg-background" defaultValue="2023-01-01" />
                    </div>
                    <div className="space-y-1.5">
                      <label className="text-[10px] font-bold text-muted-foreground uppercase">End Date</label>
                      <Input type="date" className="h-8 text-xs font-mono bg-background" defaultValue="2023-10-01" />
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
                    <Input type="number" className="h-8 text-xs font-mono bg-background" defaultValue="10000" />
                  </div>
                  <div className="space-y-2">
                    <div className="flex justify-between">
                      <label className="text-[10px] font-bold text-muted-foreground uppercase">Risk Per Trade</label>
                      <span className="text-xs font-mono text-destructive font-bold">{risk[0]}%</span>
                    </div>
                    <Slider value={risk} onValueChange={setRisk} min={0.5} max={10} step={0.5} />
                  </div>
                  <Button className="w-full font-bold uppercase tracking-wider" onClick={handleRunTest} disabled={runTest.isPending}>
                    {runTest.isPending ? "Running..." : <><Play className="w-4 h-4 mr-2" />Run Backtest</>}
                  </Button>
                </div>
              </div>

              {/* Backtest History */}
              <div className="col-span-1 lg:col-span-2 rounded-lg border border-border bg-card overflow-hidden flex flex-col">
                <div className="px-4 py-3 border-b border-border bg-secondary/20 shrink-0">
                  <p className="text-xs font-bold uppercase tracking-wider">Backtest History</p>
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
                        <TableHead className="font-mono text-[10px] uppercase text-muted-foreground text-right">P&amp;L</TableHead>
                        <TableHead className="font-mono text-[10px] uppercase text-muted-foreground text-center">Action</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {isTestsLoading ? (
                        <TableRow><TableCell colSpan={7} className="text-center py-8 text-xs text-muted-foreground">Loading...</TableCell></TableRow>
                      ) : (
                        backtests?.map((bt) => (
                          <TableRow key={bt.id} className="border-b border-border/50 hover:bg-secondary/20">
                            <TableCell className="font-mono text-xs font-bold">{bt.id}</TableCell>
                            <TableCell className="font-mono text-xs text-muted-foreground">{bt.date}</TableCell>
                            <TableCell className="font-mono text-xs text-right">{bt.tradeCount}</TableCell>
                            <TableCell className="font-mono text-xs text-right font-bold text-primary">{bt.winRate}%</TableCell>
                            <TableCell className="font-mono text-xs text-right">{bt.profitFactor}</TableCell>
                            <TableCell className={`font-mono text-xs text-right font-bold ${bt.pnlAmount >= 0 ? "text-accent" : "text-destructive"}`}>
                              ${bt.pnlAmount.toFixed(2)}
                            </TableCell>
                            <TableCell className="text-center">
                              <Button variant="outline" size="sm" className="h-7 text-[10px] uppercase px-2 font-bold hover:bg-secondary">
                                Load
                              </Button>
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
                  <TableHead className="font-mono text-[10px] uppercase text-muted-foreground w-[150px]">Time</TableHead>
                  <TableHead className="font-mono text-[10px] uppercase text-muted-foreground">Type</TableHead>
                  <TableHead className="font-mono text-[10px] uppercase text-muted-foreground text-right">Price</TableHead>
                  <TableHead className="font-mono text-[10px] uppercase text-muted-foreground">Context</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {isSignalsLoading ? (
                  <TableRow><TableCell colSpan={4} className="text-center py-8 text-xs text-muted-foreground">Loading signals...</TableCell></TableRow>
                ) : (
                  signals?.filter((s) => s.source === "RSI EMA").map((sig) => (
                    <TableRow key={sig.id} className="border-border hover:bg-secondary/20">
                      <TableCell className="font-mono text-xs text-muted-foreground">{sig.time}</TableCell>
                      <TableCell>
                        <Badge
                          variant={sig.type === "BUY" ? "default" : "destructive"}
                          className="text-[10px] font-bold w-16 justify-center"
                        >
                          {sig.type}
                        </Badge>
                      </TableCell>
                      <TableCell className="font-mono text-xs text-right font-bold">{sig.price.toFixed(2)}</TableCell>
                      <TableCell className="text-xs text-muted-foreground">{sig.context}</TableCell>
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
