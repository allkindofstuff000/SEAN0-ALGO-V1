import { useEffect, useMemo, useRef } from "react";
import Chart from "chart.js/auto";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { RsiBacktestResult } from "@/lib/api";

const GREEN = "#26a69a";
const RED = "#ef5350";
const GREY = "#6b7280";
const GRID = "rgba(255,255,255,0.06)";
const TICK = "#8b8f9a";

const money = (v: number) =>
  (v < 0 ? "-" : "") + "$" + Math.abs(Math.round(v)).toLocaleString();

function MetricCard({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: "good" | "bad" | "neutral" }) {
  const color = tone === "good" ? "text-accent" : tone === "bad" ? "text-destructive" : "text-foreground";
  return (
    <div className="bg-secondary/30 rounded-lg px-4 py-3 border border-border/40">
      <p className="text-[10px] uppercase font-bold text-muted-foreground tracking-wider mb-1">{label}</p>
      <p className={`font-mono text-xl font-bold ${color}`}>{value}{sub && <span className="text-xs font-normal ml-1 text-muted-foreground">{sub}</span>}</p>
    </div>
  );
}

export function BacktestResults({ result, startBalance }: { result: RsiBacktestResult; startBalance: number }) {
  const eqRef = useRef<HTMLCanvasElement | null>(null);
  const ddRef = useRef<HTMLCanvasElement | null>(null);
  const plRef = useRef<HTMLCanvasElement | null>(null);
  const charts = useRef<Chart[]>([]);

  const m = result.metrics;
  const trades = result.trades || [];
  const netProfit = m.ending_balance - startBalance;
  const netPct = startBalance ? (netProfit / startBalance) * 100 : 0;

  // Build equity series (start + after each trade) and drawdown %
  const { labels, equity, drawdown, maxDDpct, pnls, plColors } = useMemo(() => {
    const eq = [startBalance, ...trades.map((t) => t.equity_after)];
    const labels = ["start", ...trades.map((_, i) => `#${i + 1}`)];
    let peak = -Infinity;
    const dd = eq.map((e) => {
      peak = Math.max(peak, e);
      return peak > 0 ? Math.round(((e - peak) / peak) * 10000) / 100 : 0;
    });
    const pnls = trades.map((t) => t.pnl);
    const plColors = trades.map((t) => (t.pnl >= 0 ? GREEN : RED));
    return { labels, equity: eq, drawdown: dd, maxDDpct: Math.min(...dd), pnls, plColors };
  }, [trades, startBalance]);

  useEffect(() => {
    charts.current.forEach((c) => c.destroy());
    charts.current = [];
    if (!eqRef.current || !ddRef.current || !plRef.current) return;

    const baseScale = {
      x: { grid: { color: GRID }, ticks: { color: TICK, maxRotation: 45, font: { size: 10 }, autoSkip: true, maxTicksLimit: 12 } },
      y: { grid: { color: GRID }, ticks: { color: TICK, font: { size: 10 } } },
    };

    charts.current.push(new Chart(eqRef.current, {
      type: "line",
      data: {
        labels,
        datasets: [
          { data: equity, borderColor: GREEN, backgroundColor: "rgba(38,166,154,0.12)", fill: true, tension: 0.25, pointRadius: 1.5, pointBackgroundColor: GREEN, borderWidth: 2 },
          { data: equity.map(() => startBalance), borderColor: GREY, borderDash: [6, 4], pointRadius: 0, borderWidth: 1, fill: false },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: { duration: 400 },
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => (c.datasetIndex === 0 ? "Equity: " + money(c.parsed.y) : "Start: " + money(startBalance)) } } },
        scales: { x: baseScale.x, y: { ...baseScale.y, ticks: { ...baseScale.y.ticks, callback: (v) => money(v as number) } } },
      },
    }));

    charts.current.push(new Chart(ddRef.current, {
      type: "line",
      data: { labels, datasets: [{ data: drawdown, borderColor: RED, backgroundColor: "rgba(239,83,80,0.15)", fill: "origin", tension: 0.2, pointRadius: 0, borderWidth: 1.5 }] },
      options: {
        responsive: true, maintainAspectRatio: false, animation: { duration: 400 },
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => "Drawdown: " + (c.parsed.y as number).toFixed(2) + "%" } } },
        scales: { x: baseScale.x, y: { ...baseScale.y, max: 0.5, ticks: { ...baseScale.y.ticks, callback: (v) => v + "%" } } },
      },
    }));

    charts.current.push(new Chart(plRef.current, {
      type: "bar",
      data: { labels: trades.map((_, i) => `#${i + 1}`), datasets: [{ data: pnls, backgroundColor: plColors, borderRadius: 2 }] },
      options: {
        responsive: true, maintainAspectRatio: false, animation: { duration: 400 },
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => money(c.parsed.y) + "  (" + (trades[c.dataIndex]?.exit_timestamp || "").slice(5, 16) + ")" } } },
        scales: { x: { grid: { display: false }, ticks: { color: TICK, font: { size: 9 }, autoSkip: true, maxTicksLimit: 25 } }, y: { ...baseScale.y, ticks: { ...baseScale.y.ticks, callback: (v) => money(v as number) } } },
      },
    }));

    return () => { charts.current.forEach((c) => c.destroy()); charts.current = []; };
  }, [labels, equity, drawdown, pnls, plColors, trades, startBalance]);

  const fmt = (n: number, d = 2) => (Number.isFinite(n) ? n.toFixed(d) : "—");
  const pf = m.profit_factor;

  return (
    <div className="space-y-4">
      {/* Metric cards */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        <MetricCard label="Ending Balance" value={money(m.ending_balance)} tone={netProfit >= 0 ? "good" : "bad"} />
        <MetricCard label="Net Profit" value={(netProfit >= 0 ? "+" : "") + money(netProfit)} sub={`${netPct >= 0 ? "+" : ""}${fmt(netPct)}%`} tone={netProfit >= 0 ? "good" : "bad"} />
        <MetricCard label="Win Rate" value={`${fmt(m.win_rate, 1)}%`} sub={`${m.wins}W/${m.losses}L`} tone="neutral" />
        <MetricCard label="Profit Factor" value={Number.isFinite(pf) ? fmt(pf) : "∞"} tone={pf >= 1 ? "good" : "bad"} />
        <MetricCard label="Max Drawdown" value={`${fmt(maxDDpct)}%`} sub={`${fmt(m.max_drawdown_r)}R`} tone="bad" />
        <MetricCard label="Avg R / Trade" value={`${m.average_r >= 0 ? "+" : ""}${fmt(m.average_r)}`} sub={`${m.total_trades} trades`} tone={m.average_r >= 0 ? "good" : "bad"} />
      </div>

      {/* Equity curve */}
      <div className="rounded-lg border border-border bg-card p-4">
        <p className="text-xs font-bold uppercase tracking-wider mb-3 flex items-center gap-2">
          <span className="w-2 h-2 rounded-full" style={{ background: GREEN }} />Equity Curve
          <span className="text-[10px] font-normal text-muted-foreground normal-case ml-1">start {money(startBalance)} · dashed = breakeven</span>
        </p>
        <div className="relative h-[240px]"><canvas ref={eqRef} role="img" aria-label="Equity curve over trades" /></div>
      </div>

      {/* Drawdown + PnL side by side on wide screens */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="rounded-lg border border-border bg-card p-4">
          <p className="text-xs font-bold uppercase tracking-wider mb-3 flex items-center gap-2">
            <span className="w-2 h-2 rounded-full" style={{ background: RED }} />Drawdown from Peak (%)
          </p>
          <div className="relative h-[180px]"><canvas ref={ddRef} role="img" aria-label="Drawdown percentage from peak" /></div>
        </div>
        <div className="rounded-lg border border-border bg-card p-4">
          <p className="text-xs font-bold uppercase tracking-wider mb-3 flex items-center gap-3">
            Per-Trade P&amp;L
            <span className="text-[10px] font-normal normal-case flex items-center gap-1"><span className="w-2 h-2 rounded-full" style={{ background: GREEN }} />Win</span>
            <span className="text-[10px] font-normal normal-case flex items-center gap-1"><span className="w-2 h-2 rounded-full" style={{ background: RED }} />Loss</span>
          </p>
          <div className="relative h-[180px]"><canvas ref={plRef} role="img" aria-label="Profit and loss per trade" /></div>
        </div>
      </div>

      {/* Full trades table */}
      <div className="rounded-lg border border-border bg-card overflow-hidden">
        <div className="px-4 py-3 border-b border-border bg-secondary/20 flex items-center justify-between">
          <p className="text-xs font-bold uppercase tracking-wider">All Trades ({trades.length})</p>
          <p className="text-[10px] text-muted-foreground font-mono">{m.wins} wins · {m.losses} losses</p>
        </div>
        <div className="overflow-auto max-h-[420px]">
          <Table>
            <TableHeader className="sticky top-0 bg-card z-10">
              <TableRow className="hover:bg-transparent border-border">
                {["#", "Exit Time", "Dir", "Entry", "Exit", "SL", "TP", "P&L", "R", "Result", "Exit", "Bars"].map((h) => (
                  <TableHead key={h} className={`font-mono text-[10px] uppercase text-muted-foreground ${["Entry", "Exit", "SL", "TP", "P&L", "R", "Bars"].includes(h) ? "text-right" : ""}`}>{h}</TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {trades.length === 0 ? (
                <TableRow><TableCell colSpan={12} className="text-center py-8 text-xs text-muted-foreground">No trades in this window.</TableCell></TableRow>
              ) : (
                trades.map((t, i) => (
                  <TableRow key={i} className="border-border/50 hover:bg-secondary/20">
                    <TableCell className="font-mono text-xs font-bold">{i + 1}</TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">{(t.exit_timestamp || "").slice(0, 16).replace("T", " ")}</TableCell>
                    <TableCell><Badge variant={t.direction === "BUY" ? "default" : "destructive"} className="text-[9px] font-bold w-12 justify-center">{t.direction}</Badge></TableCell>
                    <TableCell className="font-mono text-xs text-right">{t.entry_price?.toFixed(2)}</TableCell>
                    <TableCell className="font-mono text-xs text-right">{t.exit_price?.toFixed(2)}</TableCell>
                    <TableCell className="font-mono text-xs text-right text-destructive/80">{t.sl?.toFixed(2)}</TableCell>
                    <TableCell className="font-mono text-xs text-right text-accent/80">{t.tp?.toFixed(2)}</TableCell>
                    <TableCell className={`font-mono text-xs text-right font-bold ${t.pnl >= 0 ? "text-accent" : "text-destructive"}`}>{(t.pnl >= 0 ? "+" : "") + money(t.pnl)}</TableCell>
                    <TableCell className={`font-mono text-xs text-right ${t.R_multiple >= 0 ? "text-accent" : "text-destructive"}`}>{(t.R_multiple >= 0 ? "+" : "") + t.R_multiple.toFixed(2)}</TableCell>
                    <TableCell><Badge variant="outline" className={`text-[9px] font-bold ${t.result === "WIN" ? "text-accent border-accent/30 bg-accent/10" : "text-destructive border-destructive/30 bg-destructive/10"}`}>{t.result}</Badge></TableCell>
                    <TableCell className="text-[10px] text-muted-foreground font-mono">{(t.exit_reason || "").replace(/_/g, " ").replace("hit", "").trim()}</TableCell>
                    <TableCell className="font-mono text-xs text-right text-muted-foreground">{t.bars_held}</TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
      </div>
    </div>
  );
}
