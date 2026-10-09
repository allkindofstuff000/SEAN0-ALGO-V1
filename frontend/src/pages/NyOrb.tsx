import { PageLayout } from "@/components/layout/PageLayout";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useCryptoLivePrice, useLiveSignals, useBacktestHistory, useBotStatus, useRunNyorbBacktest } from "@/hooks/use-trading-data";
import { Fragment, useState } from "react";
import { Play, History, Send, Clock, BarChart2, Ruler } from "lucide-react";
import { useToast } from "@/hooks/use-toast";
import { BacktestResults } from "@/components/BacktestResults";
import { SignalDetail } from "@/components/SignalDetail";
import { Api, cryptoSpec, type CryptoKey, type RsiBacktestResult } from "@/lib/api";
import { fmtLocal, TZ_LABEL } from "@/lib/tz";

// NY opening-range breakout: range 13:30-14:30 UTC (19:30-20:30 Dhaka), first M5 close
// beyond it, stop at the far side of the range, target 1.5R, flat at 21:00 UTC.

const TABS = [
  { id: "backtest", label: "Backtest", icon: Play },
  { id: "signals", label: "Signal History", icon: History },
];

const isoDaysAgo = (n: number) => {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - n);
  return d.toISOString().split("T")[0];
};

function signalPnl(s: { direction?: string; entry_price?: number; exit_price?: number | null; stop_loss?: number | null; outcome?: string | null }) {
  if (!s.outcome || s.exit_price == null || s.entry_price == null) return null;
  const isBuy = (s.direction || "").toUpperCase() === "BUY";
  const pips = isBuy ? s.exit_price - s.entry_price : s.entry_price - s.exit_price;
  const risk = s.stop_loss != null ? Math.abs(s.entry_price - s.stop_loss) : 0;
  return { pips, r: risk > 0 ? pips / risk : 0 };
}

export default function NyOrb({ symbolKey = "ETH" }: { symbolKey?: CryptoKey }) {
  const sym = symbolKey;
  const spec = cryptoSpec(sym);
  const strategyTag = `nyorb-${sym.toLowerCase()}`;
  const botKey = `nyorb${sym.charAt(0)}${sym.slice(1).toLowerCase()}`; // nyorbEth
  const { toast } = useToast();
  const [activeTab, setActiveTab] = useState("backtest");
  const [tpR, setTpR] = useState([1.5]);
  const [rangeMin, setRangeMin] = useState(60);
  const [longOnly, setLongOnly] = useState(false);
  const [risk, setRisk] = useState([2]);
  const [startDate, setStartDate] = useState(isoDaysAgo(90));
  const [endDate, setEndDate] = useState(isoDaysAgo(0));
  const [balance, setBalance] = useState(10000);
  const [result, setResult] = useState<RsiBacktestResult | null>(null);
  const [ranBalance, setRanBalance] = useState(10000);
  const [expandedSig, setExpandedSig] = useState<string | null>(null);
  const [loadingReport, setLoadingReport] = useState<string | null>(null);

  const { data: livePrice } = useCryptoLivePrice(sym);
  const { data: signalsData } = useLiveSignals(100);
  const { data: history } = useBacktestHistory();
  const { data: botStatus } = useBotStatus();
  const runBacktest = useRunNyorbBacktest(sym);
  const running = !!(botStatus as any)?.[botKey]?.running;

  const reports = (history?.reports || []).filter((r) => r.params?.strategy === strategyTag && r.metrics?.total_trades != null);
  const signals = (signalsData?.signals || []).filter((s) => `${s.strategy || ""} ${s.signal_kind || ""}`.toLowerCase().includes("nyorb") && (s.symbol || "").toUpperCase() === spec.display);
  const resolved = signals.filter((s) => s.outcome === "WIN" || s.outcome === "LOSS");
  const wins = resolved.filter((s) => s.outcome === "WIN").length;
  const losses = resolved.filter((s) => s.outcome === "LOSS").length;
  const winRate = resolved.length ? (wins / resolved.length) * 100 : 0;
  const netR = resolved.reduce((acc, s) => acc + (signalPnl(s)?.r ?? 0), 0);

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

  const handleRun = () => {
    const bal = balance;
    runBacktest.mutate(
      { start_date: startDate, end_date: endDate, tp_r: tpR[0], range_minutes: rangeMin, long_only: longOnly, starting_balance: balance, risk_per_trade_pct: risk[0] },
      {
        onSuccess: (res) => {
          if ((res as any).error) {
            toast({ title: "Backtest error", description: (res as any).error, variant: "destructive" });
            return;
          }
          setResult(res);
          setRanBalance(bal);
          const m = res.metrics;
          toast({ title: `${sym} NY range backtest complete`, description: `${m.total_trades} trades · ${m.win_rate.toFixed(1)}% win · PF ${m.profit_factor.toFixed(2)}` });
        },
        onError: (e: any) => toast({ title: "Backtest failed", description: String(e.message || e), variant: "destructive" }),
      },
    );
  };

  return (
    <PageLayout>
      <div className="flex items-center border-b border-border/60 bg-card/50 px-4 shrink-0">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button key={id} onClick={() => setActiveTab(id)} className={`flex items-center gap-1.5 px-4 py-3 text-xs font-bold uppercase tracking-wider border-b-2 transition-colors ${activeTab === id ? "border-primary text-primary" : "border-transparent text-muted-foreground hover:text-foreground"}`}>
            <Icon className="w-3.5 h-3.5" />
            {label}
          </button>
        ))}
      </div>

      <div className="px-4 py-2 border-b border-border/30 shrink-0 flex items-center gap-2 flex-wrap">
        <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full border text-xs font-bold bg-accent/10 border-accent/20 text-accent">
          <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
          {spec.display} · NY session · <span className="text-accent">19:30 → 03:00 {TZ_LABEL}</span>
        </div>
        <span className="text-[11px] text-muted-foreground hidden sm:inline">Range 13:30-14:30 UTC · first M5 close beyond it · stop at the far side · target 1.5R · flat at 21:00 UTC</span>
      </div>

      <div className="mx-4 mt-3 rounded-lg border border-border bg-card px-5 py-3 flex items-center justify-between shrink-0">
        <div>
          <div className="flex items-center gap-2">
            <Ruler className="w-4 h-4 text-primary" />
            <span className="font-bold text-lg tracking-tight">{spec.pair}</span>
            {running && <Badge className="bg-accent/20 text-accent border-accent/40 text-[10px] font-bold px-2 animate-pulse">LIVE</Badge>}
            <Badge className="bg-primary/20 text-primary border-primary/40 text-[10px] font-bold px-2">NY OPEN RANGE</Badge>
          </div>
          <p className="text-xs text-muted-foreground mt-0.5">Opening-range breakout · one trade per direction per day · Binance M5</p>
        </div>
        <div className="text-right">
          <div className="font-mono text-2xl font-bold tracking-tight">{livePrice?.price ? livePrice.price.toFixed(2) : "—"}</div>
          <div className="text-[10px] text-muted-foreground">live</div>
        </div>
      </div>

      <div className="flex-1 mx-4 mt-2 mb-4 min-h-0 overflow-hidden">
        {activeTab === "backtest" && (
          <div className="h-full overflow-auto pb-4 space-y-4">
            <div className="rounded-lg border border-border bg-card p-4">
              <div className="flex items-center justify-between mb-3">
                <p className="text-xs font-bold uppercase tracking-wider">Backtest Configuration</p>
                <p className="text-[10px] font-mono text-muted-foreground">{spec.binance} M5 · Binance history · 0.06% round-trip cost · stop-first</p>
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
                  <div className="flex justify-between"><label className="text-[10px] font-bold text-muted-foreground uppercase">Target</label><span className="text-xs font-mono text-primary font-bold">{tpR[0].toFixed(1)}R</span></div>
                  <Slider value={tpR} onValueChange={setTpR} min={1} max={3} step={0.5} />
                </div>
                <div className="space-y-2">
                  <div className="flex justify-between"><label className="text-[10px] font-bold text-muted-foreground uppercase">Risk</label><span className="text-xs font-mono text-destructive font-bold">{risk[0]}%</span></div>
                  <Slider value={risk} onValueChange={setRisk} min={1} max={10} step={0.5} />
                </div>
                <div className="space-y-1.5">
                  <label className="text-[10px] font-bold text-muted-foreground uppercase">Range</label>
                  <div className="flex gap-1">
                    {[30, 60].map((m) => (
                      <button key={m} onClick={() => setRangeMin(m)} className={`flex-1 h-8 rounded text-xs font-bold border ${rangeMin === m ? "bg-primary text-primary-foreground border-primary" : "border-border text-muted-foreground hover:text-foreground"}`}>{m} min</button>
                    ))}
                  </div>
                </div>
              </div>
              <div className="flex flex-col sm:flex-row gap-2 mt-4 items-center">
                <Button className="flex-1 w-full font-bold uppercase tracking-wider" onClick={handleRun} disabled={runBacktest.isPending}>
                  {runBacktest.isPending ? "Running backtest…" : <><Play className="w-4 h-4 mr-2" />Run Backtest</>}
                </Button>
                <label className="flex items-center gap-2 text-xs font-bold text-muted-foreground cursor-pointer px-2">
                  <input type="checkbox" checked={longOnly} onChange={(e) => setLongOnly(e.target.checked)} className="accent-primary" />
                  Long only
                </label>
              </div>
              <p className="text-[10px] text-muted-foreground mt-2">Validated 2026-10-09 on ETH: 60-min range, 1.5R target → 47% win, PF 1.23, positive in 5 of 6 windows. Thin edge, sensitive to spread.</p>
            </div>

            {runBacktest.isPending ? (
              <div className="rounded-lg border border-border bg-card p-12 text-center">
                <div className="inline-block w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin mb-3" />
                <p className="text-xs text-muted-foreground">Replaying the NY range breakout over {spec.binance} history…</p>
              </div>
            ) : result ? (
              <BacktestResults result={result} startBalance={ranBalance} />
            ) : (
              <div className="rounded-lg border border-border border-dashed bg-card/50 p-12 text-center">
                <BarChart2 className="w-10 h-10 text-muted-foreground mx-auto mb-3" />
                <p className="font-bold text-sm uppercase tracking-wider">No backtest yet</p>
                <p className="text-xs text-muted-foreground mt-1">Pick a window, then run the breakout rules on {sym}.</p>
              </div>
            )}

            <div className="rounded-lg border border-border bg-card overflow-hidden">
              <div className="px-4 py-3 border-b border-border bg-secondary/20 flex items-center gap-2">
                <Clock className="w-3.5 h-3.5 text-muted-foreground" />
                <p className="text-xs font-bold uppercase tracking-wider">{sym} NY Range Backtest History</p>
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
                    {reports.length === 0 ? (
                      <TableRow><TableCell colSpan={8} className="text-center py-8 text-xs text-muted-foreground">No runs yet. Results are stored automatically.</TableCell></TableRow>
                    ) : (
                      reports.map((r) => {
                        const m = r.metrics;
                        const start = Number(r.params?.starting_balance) || 0;
                        const profit = (m.ending_balance ?? 0) - start;
                        return (
                          <TableRow key={r._id} className="border-border/50 hover:bg-secondary/20 cursor-pointer" onClick={() => openReport(r._id)}>
                            <TableCell className="font-mono text-xs text-muted-foreground">{fmtLocal(r.saved_at)}</TableCell>
                            <TableCell className="font-mono text-[11px] text-muted-foreground">{r.params?.start_date || "?"} → {r.params?.end_date || "?"}</TableCell>
                            <TableCell className="font-mono text-xs text-right">{m.total_trades ?? 0}</TableCell>
                            <TableCell className="font-mono text-xs text-right font-bold text-primary">{(m.win_rate ?? 0).toFixed(1)}%</TableCell>
                            <TableCell className="font-mono text-xs text-right">{Number.isFinite(m.profit_factor) ? (m.profit_factor ?? 0).toFixed(2) : "∞"}</TableCell>
                            <TableCell className="font-mono text-xs text-right text-destructive">{(m.max_drawdown_r ?? 0).toFixed(2)}</TableCell>
                            <TableCell className={`font-mono text-xs text-right font-bold ${profit >= 0 ? "text-accent" : "text-destructive"}`}>${(m.ending_balance ?? 0).toFixed(0)}</TableCell>
                            <TableCell className="text-right"><span className="text-[10px] text-primary font-bold uppercase">{loadingReport === r._id ? "Loading…" : "View →"}</span></TableCell>
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

        {activeTab === "signals" && (
          <div className="rounded-lg border border-border bg-card overflow-hidden">
            <div className="px-4 py-3 border-b border-border bg-secondary/20 flex flex-col sm:flex-row sm:items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <Send className="w-3.5 h-3.5 text-muted-foreground" />
                <p className="text-xs font-bold uppercase tracking-wider">Signal History</p>
                <p className="text-[10px] text-muted-foreground font-mono ml-1 hidden md:inline">{sym} NY range breakouts · resolved at stop, target or the 21:00 UTC flat</p>
              </div>
              <div className="flex items-center gap-3 text-[11px] font-mono">
                <span className="text-accent font-bold">{wins}W</span>
                <span className="text-destructive font-bold">{losses}L</span>
                <span className="text-muted-foreground">{signals.length - resolved.length} open</span>
                <span className="h-3 w-px bg-border/60" />
                <span className="text-foreground font-bold">{winRate.toFixed(0)}% win</span>
                <span className={`font-bold ${netR >= 0 ? "text-accent" : "text-destructive"}`}>{netR >= 0 ? "+" : ""}{netR.toFixed(2)}R</span>
              </div>
            </div>
            <div className="overflow-auto max-h-[560px]">
              <Table>
                <TableHeader className="sticky top-0 bg-card z-10">
                  <TableRow className="border-border hover:bg-transparent bg-secondary/30">
                    {[`Time (${TZ_LABEL})`, "Dir", "Entry", "SL", "TP", "Range", "TG", "Outcome", "P&L"].map((h) => (
                      <TableHead key={h} className={`font-mono text-[10px] uppercase text-muted-foreground ${["Entry", "SL", "TP", "P&L"].includes(h) ? "text-right" : ""}`}>{h}</TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {signals.length === 0 ? (
                    <TableRow><TableCell colSpan={9} className="text-center py-10 text-xs text-muted-foreground">No signals yet. The first one fires on the first 5-minute close beyond today's 13:30-14:30 UTC range.</TableCell></TableRow>
                  ) : (
                    signals.map((s) => {
                      const isBuy = (s.direction || "").toUpperCase() === "BUY";
                      const open = expandedSig === s._id;
                      const pnl = signalPnl(s);
                      return (
                        <Fragment key={s._id}>
                          <TableRow className="border-border/50 hover:bg-secondary/20 cursor-pointer" onClick={() => setExpandedSig(open ? null : s._id)}>
                            <TableCell className="font-mono text-[11px] text-muted-foreground"><span className="inline-block w-3 text-primary">{open ? "▾" : "▸"}</span>{fmtLocal(s.sent_at ?? s.timestamp)}</TableCell>
                            <TableCell><Badge variant={isBuy ? "default" : "destructive"} className="text-[9px] font-bold w-12 justify-center">{s.direction}</Badge></TableCell>
                            <TableCell className="font-mono text-xs text-right font-bold">{s.entry_price?.toFixed?.(2) ?? "—"}</TableCell>
                            <TableCell className="font-mono text-xs text-right text-destructive/80">{s.stop_loss != null ? s.stop_loss.toFixed(2) : "—"}</TableCell>
                            <TableCell className="font-mono text-xs text-right text-accent/80">{s.take_profit != null ? s.take_profit.toFixed(2) : "—"}</TableCell>
                            <TableCell className="font-mono text-[11px] text-muted-foreground">{(s as any).range_low != null ? `${Number((s as any).range_low).toFixed(2)} – ${Number((s as any).range_high).toFixed(2)}` : "—"}</TableCell>
                            <TableCell>{s.telegram_sent === false ? <Badge variant="outline" className="text-[9px] text-muted-foreground">logged</Badge> : <Badge variant="outline" className="text-[9px] text-accent border-accent/30 bg-accent/10">sent</Badge>}</TableCell>
                            <TableCell>
                              {s.outcome ? (
                                <Badge variant="outline" title={s.outcome_note || undefined} className={`text-[9px] font-bold ${s.outcome === "WIN" ? "text-accent border-accent/30 bg-accent/10" : s.outcome === "LOSS" ? "text-destructive border-destructive/30 bg-destructive/10" : "text-muted-foreground"}`}>{s.outcome}</Badge>
                              ) : (
                                <span className="text-[10px] text-muted-foreground animate-pulse">● open</span>
                              )}
                            </TableCell>
                            <TableCell className="text-right">
                              {pnl ? (
                                <span className={`font-mono text-xs font-bold ${pnl.r >= 0 ? "text-accent" : "text-destructive"}`}>{pnl.r >= 0 ? "+" : ""}{pnl.r.toFixed(2)}R</span>
                              ) : (
                                <span className="text-[10px] text-muted-foreground">—</span>
                              )}
                            </TableCell>
                          </TableRow>
                          {open && (
                            <TableRow className="hover:bg-transparent">
                              <TableCell colSpan={9} className="p-0"><SignalDetail s={s} /></TableCell>
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
