import { PageLayout } from "@/components/layout/PageLayout";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { CheckCircle2, AlertCircle, BarChart2, Settings, History, Zap } from "lucide-react";
import { useState } from "react";

const TABS = [
  { id: "chart", label: "Chart", icon: BarChart2 },
  { id: "setup", label: "Live Setup", icon: Settings },
  { id: "history", label: "Signal History", icon: History },
];

const CONDITIONS = [
  { label: "Session", val: "NEW YORK", status: "ACTIVE" },
  { label: "15M Structure", val: "BULLISH", status: "PASSED" },
  { label: "5M OB/FVG", val: "PRESENT", status: "PASSED" },
  { label: "Liquidity Sweep", val: "WAITING", status: "WAITING" },
  { label: "Displacement", val: "—", status: "WAITING" },
  { label: "CISD", val: "—", status: "WAITING" },
  { label: "Entry FVG", val: "—", status: "WAITING" },
  { label: "PDA Check", val: "PREMIUM", status: "ACTIVE" },
];

const SIGNALS = [
  { time: "14:23:45", type: "BUY", price: "1995.20", sl: "1990.50", tp: "2005.00", status: "OPEN", pnl: "+$420.00" },
  { time: "11:42:10", type: "SELL", price: "2008.30", sl: "2013.00", tp: "1998.50", status: "CLOSED", pnl: "+$295.00" },
  { time: "09:15:00", type: "BUY", price: "1982.40", sl: "1978.00", tp: "1992.00", status: "CLOSED", pnl: "-$88.00" },
  { time: "08:30:22", type: "BUY", price: "1975.10", sl: "1970.50", tp: "1985.00", status: "CLOSED", pnl: "+$195.00" },
];

export default function XauScalp() {
  const [activeTab, setActiveTab] = useState("chart");
  const [activeTF, setActiveTF] = useState("5M");
  const [activeOverlays, setActiveOverlays] = useState<string[]>(["Structure", "FVG", "Order Block"]);

  const toggleOverlay = (o: string) =>
    setActiveOverlays((prev) => prev.includes(o) ? prev.filter((x) => x !== o) : [...prev, o]);

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
        <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-accent/10 border border-accent/20 text-xs font-bold text-accent">
          <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
          XAUUSD · NY SESSION ACTIVE · HIGH VOLATILITY EXPECTED
        </div>
      </div>

      {/* Header Card */}
      <div className="mx-4 mt-3 rounded-lg border border-border bg-card px-5 py-3 flex items-center justify-between shrink-0">
        <div>
          <div className="flex items-center gap-2">
            <Zap className="w-4 h-4 text-primary" />
            <span className="font-bold text-lg tracking-tight">XAU/USD</span>
            <Badge className="bg-accent/20 text-accent border-accent/40 text-[10px] font-bold px-2 animate-pulse">LIVE</Badge>
            <Badge className="bg-primary/20 text-primary border-primary/40 text-[10px] font-bold px-2">SCALP</Badge>
          </div>
          <p className="text-xs text-muted-foreground mt-0.5">1M / 5M ICT execution model · Order Block + FVG targeting</p>
        </div>
        <div className="flex items-center gap-8">
          <div className="text-right">
            <span className="text-[10px] text-muted-foreground uppercase font-bold block">Today's P&amp;L</span>
            <span className="font-mono text-lg font-bold text-accent">+$842.50</span>
          </div>
          <div className="text-right">
            <span className="text-[10px] text-muted-foreground uppercase font-bold block">Win Rate</span>
            <span className="font-mono text-lg font-bold">72.4%</span>
          </div>
          <div className="text-right">
            <div className="font-mono text-2xl font-bold tracking-tight">1995.20</div>
            <div className="font-mono text-sm text-accent font-bold">+4.80 (0.24%)</div>
          </div>
        </div>
      </div>

      {/* Stats Row */}
      <div className="mx-4 mt-2 grid grid-cols-4 border border-border rounded-lg overflow-hidden shrink-0">
        {[
          { label: "Session", val: "NY ACTIVE", color: "text-accent" },
          { label: "15M Structure", val: "BULLISH", color: "text-accent" },
          { label: "Signals Today", val: "4", color: "text-foreground" },
          { label: "Open Trades", val: "1", color: "text-primary" },
        ].map((s, i) => (
          <div key={s.label} className={`px-4 py-3 flex flex-col gap-1 bg-card ${i > 0 ? "border-l border-border" : ""}`}>
            <span className="text-[10px] text-muted-foreground uppercase font-bold">{s.label}</span>
            <span className={`font-mono text-sm font-bold ${s.color}`}>{s.val}</span>
          </div>
        ))}
      </div>

      {/* Content Area */}
      <div className="flex-1 mx-4 mt-2 mb-4 min-h-0 overflow-hidden">

        {/* CHART TAB */}
        {activeTab === "chart" && (
          <div className="h-full min-h-[400px] flex flex-col rounded-lg border border-border bg-[#131722] overflow-hidden">
            {/* OHLC + Overlay toolbar */}
            <div className="flex items-center justify-between px-3 py-2 border-b border-[#2A2E39] bg-[#1E222D] shrink-0 flex-wrap gap-2">
              <div className="flex items-center gap-2 text-xs font-mono text-gray-300">
                <span className="font-bold text-white">XAUUSD</span>
                <span className="text-[#555]">·</span>
                <span className="text-gray-500">Live</span>
                <span>O <span className="text-white">1994.80</span></span>
                <span>H <span className="text-white">1998.40</span></span>
                <span>L <span className="text-white">1991.20</span></span>
                <span>C <span className="text-white">1995.20</span></span>
              </div>
              <div className="flex items-center gap-1 flex-wrap">
                {["Structure", "FVG", "Order Block", "Liquidity", "CISD"].map((o) => {
                  const colors: Record<string, string> = {
                    Structure: "bg-blue-600 text-white",
                    FVG: "bg-green-600 text-white",
                    "Order Block": "bg-blue-500 text-white",
                    Liquidity: "bg-sky-500 text-white",
                    CISD: "text-gray-400 border border-gray-600 hover:text-white",
                  };
                  const active = activeOverlays.includes(o);
                  return (
                    <button
                      key={o}
                      onClick={() => toggleOverlay(o)}
                      className={`px-2 py-0.5 text-xs font-bold rounded transition-all ${active ? colors[o] : "text-gray-500 border border-gray-700 hover:text-gray-300"}`}
                    >
                      {o}
                    </button>
                  );
                })}
                <div className="h-3 w-px bg-[#2A2E39] mx-1" />
                {["Single", "Split"].map((v, i) => (
                  <button key={v} className={`px-2 py-0.5 text-xs font-bold rounded ${i === 0 ? "bg-[#2A2E39] text-white" : "text-gray-500 hover:text-white"}`}>{v}</button>
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
              {/* Timeframe selector inside chart */}
              <div className="absolute top-2 left-3 flex items-center gap-1 z-10">
                {["1M", "5M", "15M", "1H"].map((tf) => (
                  <button
                    key={tf}
                    onClick={() => setActiveTF(tf)}
                    className={`px-2.5 py-1 text-xs font-bold rounded transition-all ${activeTF === tf ? "bg-blue-600 text-white" : "text-gray-400 hover:text-white hover:bg-[#2A2E39]"}`}
                  >
                    {tf}
                  </button>
                ))}
              </div>
              <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                <span className="text-[#2A2E39] text-3xl font-bold opacity-30 select-none">XAUUSD TRADINGVIEW MOCKUP</span>
              </div>
              {/* Order Block zone */}
              <div className="absolute top-[30%] left-[22%] w-48 h-8 bg-red-500/10 border border-red-500/30 flex items-center px-2">
                <span className="text-[10px] text-red-400/60 font-mono">BEARISH OB</span>
              </div>
              <div className="absolute top-[55%] left-[30%] w-32 h-1 border-t border-dashed border-yellow-500/40">
                <span className="text-[9px] text-yellow-500/60 font-mono ml-1">BOS</span>
              </div>
              {/* Candles */}
              {[
                { x: "12%", h: 60, body: 40, up: false },
                { x: "18%", h: 50, body: 34, up: false },
                { x: "24%", h: 100, body: 68, up: true },
                { x: "30%", h: 45, body: 30, up: true },
                { x: "36%", h: 80, body: 54, up: false },
                { x: "42%", h: 55, body: 38, up: true },
                { x: "48%", h: 70, body: 50, up: true },
              ].map((c, i) => (
                <div key={i} className="absolute" style={{ bottom: "28%", left: c.x }}>
                  <div className={`w-0.5 mx-auto ${c.up ? "bg-green-500" : "bg-red-500"}`} style={{ height: c.h * 0.8 }} />
                  <div className={`w-2.5 -ml-1 ${c.up ? "bg-green-500" : "bg-red-500"}`} style={{ height: c.body * 0.6 }} />
                </div>
              ))}
              {/* Price axis */}
              <div className="absolute right-0 top-0 bottom-0 w-14 bg-[#131722] border-l border-[#2A2E39] flex flex-col justify-between py-8 font-mono text-[10px] text-gray-500 items-end pr-2 pointer-events-none">
                <span>2000.00</span>
                <span>1997.50</span>
                <span className="text-white bg-[#2A2E39] px-1 rounded -mr-1 py-0.5">1995.20</span>
                <span>1990.00</span>
                <span>1985.00</span>
              </div>
            </div>
          </div>
        )}

        {/* LIVE SETUP TAB */}
        {activeTab === "setup" && (
          <div className="h-full overflow-auto space-y-4 pb-4">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {/* Strategy Status */}
              <div className="rounded-lg border border-border bg-card p-4">
                <p className="text-xs font-bold uppercase tracking-wider border-b border-border pb-2 mb-3">Strategy Status</p>
                <div className="space-y-3">
                  {[
                    { label: "Today's Signals", val: "4", color: "text-foreground" },
                    { label: "Strong Conviction", val: "2", color: "text-primary" },
                    { label: "Medium Conviction", val: "2", color: "text-accent" },
                    { label: "Last Signal", val: "14:23:45 EST", color: "text-muted-foreground" },
                  ].map((r) => (
                    <div key={r.label} className="flex justify-between items-center">
                      <span className="text-xs text-muted-foreground uppercase">{r.label}</span>
                      <span className={`font-mono font-bold text-sm ${r.color}`}>{r.val}</span>
                    </div>
                  ))}
                </div>
              </div>

              {/* Condition Cards */}
              <div className="col-span-1 md:col-span-2 grid grid-cols-2 lg:grid-cols-4 gap-3">
                {CONDITIONS.map((c, i) => (
                  <div key={i} className="rounded-lg border border-border bg-card p-3 flex flex-col gap-2">
                    <span className="text-[10px] uppercase font-bold text-muted-foreground">{c.label}</span>
                    <span className="font-mono text-sm font-bold tracking-tight">{c.val}</span>
                    <div
                      className={`flex items-center gap-1 text-[9px] font-bold uppercase px-2 py-1 rounded border ${
                        c.status === "PASSED"
                          ? "bg-primary/10 text-primary border-primary/20"
                          : c.status === "ACTIVE"
                          ? "bg-accent/10 text-accent border-accent/20"
                          : "bg-secondary text-muted-foreground border-border"
                      }`}
                    >
                      {c.status === "PASSED" && <CheckCircle2 className="w-3 h-3" />}
                      {c.status === "WAITING" && <AlertCircle className="w-3 h-3" />}
                      {c.status}
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* ICT Decisions Matrix */}
            <div className="rounded-lg border border-border bg-card overflow-hidden">
              <div className="px-4 py-3 border-b border-border bg-secondary/20">
                <p className="text-xs font-bold uppercase tracking-wider">ICT Decisions Matrix</p>
              </div>
              <Table>
                <TableHeader>
                  <TableRow className="border-border hover:bg-transparent">
                    <TableHead className="font-mono text-[10px] uppercase text-muted-foreground w-[120px]">Time</TableHead>
                    <TableHead className="font-mono text-[10px] uppercase text-muted-foreground w-[140px]">Source</TableHead>
                    <TableHead className="font-mono text-[10px] uppercase text-muted-foreground w-[100px]">Signal</TableHead>
                    <TableHead className="font-mono text-[10px] uppercase text-muted-foreground">Context</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {[
                    { time: "14:15:00", src: "15M BIAS", signal: "BULLISH", color: "text-accent", ctx: "BOS established upwards, target set at BSL 2005.50" },
                    { time: "14:20:00", src: "1M SWEEP", signal: "DETECTED", color: "text-primary", ctx: "SSL sweep at 1995.20 into 5M Order Block" },
                    { time: "14:23:45", src: "EXECUTION", signal: "BUY +1", color: "text-accent", ctx: "Displacement up + CISD + Entry FVG formation. Triggering long." },
                  ].map((r) => (
                    <TableRow key={r.time} className="border-border hover:bg-secondary/20">
                      <TableCell className="font-mono text-xs text-muted-foreground">{r.time}</TableCell>
                      <TableCell><Badge variant="outline" className="text-[9px] font-mono">{r.src}</Badge></TableCell>
                      <TableCell className={`font-mono text-xs font-bold ${r.color}`}>{r.signal}</TableCell>
                      <TableCell className="text-xs text-muted-foreground">{r.ctx}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>
        )}

        {/* SIGNAL HISTORY TAB */}
        {activeTab === "history" && (
          <div className="rounded-lg border border-border bg-card overflow-hidden">
            <div className="px-4 py-3 border-b border-border bg-secondary/20">
              <p className="text-xs font-bold uppercase tracking-wider">Signal History</p>
            </div>
            <Table>
              <TableHeader>
                <TableRow className="border-border hover:bg-transparent bg-secondary/30">
                  <TableHead className="font-mono text-[10px] uppercase text-muted-foreground">Time</TableHead>
                  <TableHead className="font-mono text-[10px] uppercase text-muted-foreground">Type</TableHead>
                  <TableHead className="font-mono text-[10px] uppercase text-muted-foreground text-right">Entry</TableHead>
                  <TableHead className="font-mono text-[10px] uppercase text-muted-foreground text-right">SL</TableHead>
                  <TableHead className="font-mono text-[10px] uppercase text-muted-foreground text-right">TP</TableHead>
                  <TableHead className="font-mono text-[10px] uppercase text-muted-foreground">Status</TableHead>
                  <TableHead className="font-mono text-[10px] uppercase text-muted-foreground text-right">P&amp;L</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {SIGNALS.map((s) => (
                  <TableRow key={s.time} className="border-border hover:bg-secondary/20">
                    <TableCell className="font-mono text-xs text-muted-foreground">{s.time}</TableCell>
                    <TableCell>
                      <Badge variant={s.type === "BUY" ? "default" : "destructive"} className="text-[10px] font-bold w-14 justify-center">
                        {s.type}
                      </Badge>
                    </TableCell>
                    <TableCell className="font-mono text-xs text-right font-bold">{s.price}</TableCell>
                    <TableCell className="font-mono text-xs text-right text-destructive">{s.sl}</TableCell>
                    <TableCell className="font-mono text-xs text-right text-accent">{s.tp}</TableCell>
                    <TableCell>
                      <Badge
                        variant="outline"
                        className={`text-[10px] font-bold ${s.status === "OPEN" ? "text-accent border-accent/30 bg-accent/10" : "text-muted-foreground"}`}
                      >
                        {s.status}
                      </Badge>
                    </TableCell>
                    <TableCell className={`font-mono text-xs text-right font-bold ${s.pnl.startsWith("+") ? "text-accent" : "text-destructive"}`}>
                      {s.pnl}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}

      </div>
    </PageLayout>
  );
}
