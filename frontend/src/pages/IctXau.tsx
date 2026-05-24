import { PageLayout } from "@/components/layout/PageLayout";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { BarChart2, Layers, Clock, Bell, Flame } from "lucide-react";
import { useState } from "react";

const TABS = [
  { id: "chart", label: "Chart", icon: BarChart2 },
  { id: "structure", label: "Structure", icon: Layers },
  { id: "sessions", label: "Sessions", icon: Clock },
  { id: "signals", label: "Signals", icon: Bell },
];

const OVERLAYS = ["MSS", "OB", "FVG", "Liquidity", "CISD", "NDOG/NWOG"];
const ACTIVE_OVERLAYS_DEFAULT = ["MSS", "OB", "FVG"];
const OVERLAY_COLORS: Record<string, string> = {
  MSS: "bg-purple-600 text-white",
  OB: "bg-yellow-600 text-white",
  FVG: "bg-green-600 text-white",
  Liquidity: "bg-sky-500 text-white",
  CISD: "bg-orange-500 text-white",
  "NDOG/NWOG": "bg-pink-600 text-white",
};

const SIGNALS_DATA = [
  { time: "14:23:45", bias: "BULLISH", type: "LONG", price: "1995.20", sl: "1990.50", tp: "2008.00", status: "OPEN", pnl: "+$420.00" },
  { time: "11:42:00", bias: "BEARISH", type: "SHORT", price: "2015.80", sl: "2020.50", tp: "2002.00", status: "CLOSED", pnl: "+$678.00" },
  { time: "09:05:11", bias: "BULLISH", type: "LONG", price: "1975.40", sl: "1970.00", tp: "1990.00", status: "CLOSED", pnl: "-$108.00" },
  { time: "08:30:00", bias: "BULLISH", type: "LONG", price: "1968.20", sl: "1963.50", tp: "1982.00", status: "CLOSED", pnl: "+$274.00" },
];

const STRUCTURE_DATA = [
  { tf: "4H", bias: "BULLISH", mss: "YES", choch: "NO", ob: "2x", fvg: "1x", sweep: "BSL 2010" },
  { tf: "1H", bias: "BULLISH", mss: "YES", choch: "YES", ob: "4x", fvg: "3x", sweep: "SSL 1985" },
  { tf: "15M", bias: "BULLISH", mss: "NO", choch: "YES", ob: "2x", fvg: "2x", sweep: "—" },
  { tf: "5M", bias: "NEUTRAL", mss: "NO", choch: "NO", ob: "1x", fvg: "1x", sweep: "—" },
];

const SESSION_DATA = [
  { session: "Asia", time: "00:00–08:00 UTC", status: "CLOSED", range: "8.2 pts", bias: "CONSOLIDATION", signals: 0 },
  { session: "London", time: "08:00–16:00 UTC", status: "CLOSED", range: "22.4 pts", bias: "BULLISH RAID", signals: 2 },
  { session: "New York", time: "13:00–21:00 UTC", status: "ACTIVE", range: "14.8 pts", bias: "EXPANSION UP", signals: 2 },
  { session: "NY PM", time: "17:00–21:00 UTC", status: "UPCOMING", range: "—", bias: "—", signals: 0 },
];

export default function IctXau() {
  const [activeTab, setActiveTab] = useState("chart");
  const [activeTF, setActiveTF] = useState("5M");
  const [activeOverlays, setActiveOverlays] = useState<string[]>(ACTIVE_OVERLAYS_DEFAULT);

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
          XAU/USD · NY Session Active · High Volatility Expected
        </div>
      </div>

      {/* Header Card */}
      <div className="mx-4 mt-3 rounded-lg border border-border bg-card px-5 py-3 flex items-center justify-between shrink-0">
        <div>
          <div className="flex items-center gap-2">
            <Flame className="w-4 h-4 text-yellow-500" />
            <span className="font-bold text-lg tracking-tight">XAU/USD</span>
            <Badge className="bg-accent/20 text-accent border-accent/40 text-[10px] font-bold px-2">LIVE</Badge>
            <Badge className="bg-yellow-500/20 text-yellow-400 border-yellow-400/40 text-[10px] font-bold px-2">ICT XAU</Badge>
          </div>
          <p className="text-xs text-muted-foreground mt-0.5">ICT methodology · Fractal liquidity · Power of 3 model</p>
        </div>
        <div className="flex items-center gap-8">
          <div className="text-right">
            <span className="text-[10px] text-muted-foreground uppercase font-bold block">Today's P&amp;L</span>
            <span className="font-mono text-lg font-bold text-accent">+$1,264.00</span>
          </div>
          <div className="text-right">
            <span className="text-[10px] text-muted-foreground uppercase font-bold block">Win Rate</span>
            <span className="font-mono text-lg font-bold">75.0%</span>
          </div>
          <div className="text-right">
            <div className="font-mono text-2xl font-bold tracking-tight">1995.20</div>
            <div className="font-mono text-sm text-accent font-bold">+6.40 (0.32%)</div>
          </div>
        </div>
      </div>

      {/* Stats Row */}
      <div className="mx-4 mt-2 grid grid-cols-4 border border-border rounded-lg overflow-hidden shrink-0">
        {[
          { label: "PO3 Phase", val: "EXPANSION", color: "text-accent" },
          { label: "HTF MSS", val: "BULLISH", color: "text-accent" },
          { label: "Active OB", val: "4x", color: "text-primary" },
          { label: "Liquidity", val: "BSL 2010", color: "text-yellow-400" },
        ].map((s, i) => (
          <div key={s.label} className={`px-4 py-3 flex flex-col gap-1 bg-card ${i > 0 ? "border-l border-border" : ""}`}>
            <span className="text-[10px] text-muted-foreground uppercase font-bold">{s.label}</span>
            <span className={`font-mono text-sm font-bold ${s.color}`}>{s.val}</span>
          </div>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 mx-4 mt-2 mb-4 min-h-0 overflow-hidden">

        {/* CHART TAB */}
        {activeTab === "chart" && (
          <div className="h-full min-h-[400px] flex flex-col rounded-lg border border-border bg-[#131722] overflow-hidden">
            <div className="flex items-center justify-between px-3 py-2 border-b border-[#2A2E39] bg-[#1E222D] shrink-0 flex-wrap gap-2">
              <div className="flex items-center gap-2 text-xs font-mono text-gray-300">
                <span className="font-bold text-white">XAUUSD</span>
                <span className="text-[#555]">·</span>
                <span className="text-gray-500">{activeTF}</span>
                <span>O <span className="text-white">1994.80</span></span>
                <span>H <span className="text-white">1998.60</span></span>
                <span>L <span className="text-white">1991.40</span></span>
                <span>C <span className="text-white">1995.20</span></span>
              </div>
              <div className="flex items-center gap-1 flex-wrap">
                {OVERLAYS.map((o) => {
                  const active = activeOverlays.includes(o);
                  return (
                    <button
                      key={o}
                      onClick={() => toggleOverlay(o)}
                      className={`px-2 py-0.5 text-xs font-bold rounded transition-all ${active ? OVERLAY_COLORS[o] : "text-gray-500 border border-gray-700 hover:text-gray-300"}`}
                    >
                      {o}
                    </button>
                  );
                })}
                <div className="h-3 w-px bg-[#2A2E39] mx-1" />
                <button className="px-2 py-0.5 text-xs font-bold rounded bg-[#2A2E39] text-white">Single</button>
                <button className="px-2 py-0.5 text-xs font-bold rounded text-gray-500 hover:text-white">Split</button>
              </div>
            </div>
            <div
              className="flex-1 relative"
              style={{
                backgroundImage: "linear-gradient(#1E222D 1px, transparent 1px), linear-gradient(90deg, #1E222D 1px, transparent 1px)",
                backgroundSize: "60px 50px",
              }}
            >
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
                <span className="text-[#2A2E39] text-3xl font-bold opacity-30 select-none">XAUUSD ICT MOCKUP</span>
              </div>
              {/* ICT overlays */}
              <div className="absolute top-[25%] left-[18%] w-48 h-8 bg-yellow-500/10 border border-yellow-500/30 flex items-center px-2">
                <span className="text-[10px] text-yellow-400/70 font-mono">BULLISH OB · 1988.50</span>
              </div>
              <div className="absolute top-[42%] left-[32%] w-36 h-5 bg-green-500/10 border border-green-500/30 flex items-center px-2">
                <span className="text-[10px] text-green-400/70 font-mono">FVG · 1992.00</span>
              </div>
              <div className="absolute top-[60%] left-[22%] w-40 h-1 border-t border-dashed border-purple-400/50">
                <span className="text-[9px] text-purple-400/70 font-mono ml-1">MSS · BOS</span>
              </div>
              <div className="absolute top-[20%] left-[50%] w-28 h-1 border-t border-dashed border-yellow-500/40">
                <span className="text-[9px] text-yellow-500/60 font-mono ml-1">BSL 2010</span>
              </div>
              {/* Candles */}
              {[
                { x: "8%", h: 65, body: 44, up: false },
                { x: "14%", h: 48, body: 32, up: false },
                { x: "20%", h: 90, body: 62, up: true },
                { x: "26%", h: 50, body: 34, up: true },
                { x: "32%", h: 78, body: 54, up: false },
                { x: "38%", h: 62, body: 42, up: true },
                { x: "44%", h: 88, body: 60, up: true },
                { x: "50%", h: 44, body: 30, up: false },
                { x: "56%", h: 72, body: 50, up: true },
              ].map((c, i) => (
                <div key={i} className="absolute" style={{ bottom: "22%", left: c.x }}>
                  <div className={`w-0.5 mx-auto ${c.up ? "bg-green-500" : "bg-red-500"}`} style={{ height: c.h * 0.8 }} />
                  <div className={`w-2.5 -ml-1 ${c.up ? "bg-green-500" : "bg-red-500"}`} style={{ height: c.body * 0.6 }} />
                </div>
              ))}
              <div className="absolute right-0 top-0 bottom-0 w-14 bg-[#131722] border-l border-[#2A2E39] flex flex-col justify-between py-8 font-mono text-[10px] text-gray-500 items-end pr-2 pointer-events-none">
                <span>2005.00</span>
                <span>2000.00</span>
                <span className="text-white bg-[#2A2E39] px-1 rounded -mr-1 py-0.5">1995.20</span>
                <span>1990.00</span>
                <span>1985.00</span>
              </div>
            </div>
          </div>
        )}

        {/* STRUCTURE TAB */}
        {activeTab === "structure" && (
          <div className="rounded-lg border border-border bg-card overflow-hidden">
            <div className="px-4 py-3 border-b border-border bg-secondary/20">
              <p className="text-xs font-bold uppercase tracking-wider">Multi-Timeframe Structure — XAU/USD</p>
            </div>
            <Table>
              <TableHeader>
                <TableRow className="border-border hover:bg-transparent bg-secondary/30">
                  {["TF", "Bias", "MSS", "CHoCH", "OBs", "FVGs", "Liquidity Target"].map((h) => (
                    <TableHead key={h} className="font-mono text-[10px] uppercase text-muted-foreground">{h}</TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {STRUCTURE_DATA.map((r) => (
                  <TableRow key={r.tf} className="border-border hover:bg-secondary/20">
                    <TableCell className="font-mono text-xs font-bold">{r.tf}</TableCell>
                    <TableCell>
                      <Badge variant="outline" className={`text-[10px] font-bold ${r.bias === "BULLISH" ? "text-accent border-accent/30 bg-accent/10" : r.bias === "BEARISH" ? "text-destructive border-destructive/30 bg-destructive/10" : "text-muted-foreground"}`}>
                        {r.bias}
                      </Badge>
                    </TableCell>
                    <TableCell className={`font-mono text-xs font-bold ${r.mss === "YES" ? "text-accent" : "text-muted-foreground"}`}>{r.mss}</TableCell>
                    <TableCell className={`font-mono text-xs font-bold ${r.choch === "YES" ? "text-primary" : "text-muted-foreground"}`}>{r.choch}</TableCell>
                    <TableCell className="font-mono text-xs text-yellow-400 font-bold">{r.ob}</TableCell>
                    <TableCell className="font-mono text-xs text-green-400 font-bold">{r.fvg}</TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">{r.sweep}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}

        {/* SESSIONS TAB */}
        {activeTab === "sessions" && (
          <div className="rounded-lg border border-border bg-card overflow-hidden">
            <div className="px-4 py-3 border-b border-border bg-secondary/20">
              <p className="text-xs font-bold uppercase tracking-wider">Session Analysis — XAU/USD</p>
            </div>
            <Table>
              <TableHeader>
                <TableRow className="border-border hover:bg-transparent bg-secondary/30">
                  {["Session", "Time (UTC)", "Status", "Range", "Bias", "Signals"].map((h) => (
                    <TableHead key={h} className="font-mono text-[10px] uppercase text-muted-foreground">{h}</TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {SESSION_DATA.map((s) => (
                  <TableRow key={s.session} className="border-border hover:bg-secondary/20">
                    <TableCell className="font-mono text-xs font-bold">{s.session}</TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">{s.time}</TableCell>
                    <TableCell>
                      <Badge variant="outline" className={`text-[10px] font-bold ${s.status === "ACTIVE" ? "text-accent border-accent/30 bg-accent/10" : s.status === "UPCOMING" ? "text-primary border-primary/30 bg-primary/10" : "text-muted-foreground"}`}>
                        {s.status === "ACTIVE" && <span className="w-1.5 h-1.5 rounded-full bg-accent inline-block mr-1 animate-pulse" />}
                        {s.status}
                      </Badge>
                    </TableCell>
                    <TableCell className="font-mono text-xs">{s.range}</TableCell>
                    <TableCell className={`font-mono text-xs font-bold ${s.bias.includes("BULLISH") || s.bias.includes("EXPANSION") ? "text-accent" : s.bias === "CONSOLIDATION" ? "text-primary" : "text-muted-foreground"}`}>{s.bias}</TableCell>
                    <TableCell className="font-mono text-xs font-bold">{s.signals}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}

        {/* SIGNALS TAB */}
        {activeTab === "signals" && (
          <div className="rounded-lg border border-border bg-card overflow-hidden">
            <div className="px-4 py-3 border-b border-border bg-secondary/20">
              <p className="text-xs font-bold uppercase tracking-wider">ICT Signal Log — XAU/USD</p>
            </div>
            <Table>
              <TableHeader>
                <TableRow className="border-border hover:bg-transparent bg-secondary/30">
                  {["Time", "Bias", "Direction", "Entry", "SL", "TP", "Status", "P&L"].map((h) => (
                    <TableHead key={h} className={`font-mono text-[10px] uppercase text-muted-foreground ${["Entry","SL","TP","P&L"].includes(h) ? "text-right" : ""}`}>{h}</TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {SIGNALS_DATA.map((s) => (
                  <TableRow key={s.time} className="border-border hover:bg-secondary/20">
                    <TableCell className="font-mono text-xs text-muted-foreground">{s.time}</TableCell>
                    <TableCell>
                      <Badge variant="outline" className={`text-[10px] font-bold ${s.bias === "BULLISH" ? "text-accent border-accent/30" : "text-destructive border-destructive/30"}`}>
                        {s.bias}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <Badge variant={s.type === "LONG" ? "default" : "destructive"} className="text-[10px] font-bold w-14 justify-center">
                        {s.type}
                      </Badge>
                    </TableCell>
                    <TableCell className="font-mono text-xs text-right font-bold">{s.price}</TableCell>
                    <TableCell className="font-mono text-xs text-right text-destructive">{s.sl}</TableCell>
                    <TableCell className="font-mono text-xs text-right text-accent">{s.tp}</TableCell>
                    <TableCell>
                      <Badge variant="outline" className={`text-[10px] font-bold ${s.status === "OPEN" ? "text-accent border-accent/30 bg-accent/10" : "text-muted-foreground"}`}>
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
