import { useState } from "react";
import { PageLayout } from "@/components/layout/PageLayout";
import { Badge } from "@/components/ui/badge";
import { BarChart2, FlaskConical, Zap, Settings2, RefreshCw, Maximize2, BarChart3 } from "lucide-react";

const TABS = [
  { id: "chart", label: "Chart", icon: BarChart2 },
  { id: "backtest", label: "Backtest", icon: FlaskConical },
  { id: "signals", label: "Signals", icon: Zap },
  { id: "optimizer", label: "Optimizer", icon: Settings2 },
];

const TOGGLES = [
  { label: "Structure", color: "bg-blue-600 text-white border-blue-600" },
  { label: "FVG", color: "bg-green-600 text-white border-green-600" },
  { label: "Order Block", color: "bg-blue-500 text-white border-blue-500" },
  { label: "Liquidity", color: "bg-sky-500 text-white border-sky-500" },
  { label: "Displacement", color: "bg-violet-500 text-white border-violet-500" },
  { label: "CISD", color: "bg-transparent text-gray-300 border-gray-500" },
  { label: "Signal", color: "bg-transparent text-pink-400 border-pink-500" },
  { label: "Swing", color: "bg-transparent text-gray-300 border-gray-500" },
  { label: "S/D", color: "bg-transparent text-yellow-400 border-yellow-500" },
];

function ChartArea({ symbol }: { symbol: string }) {
  const [activeToggle, setActiveToggle] = useState(new Set(["Structure", "FVG", "Order Block", "Liquidity", "Displacement"]));
  const [activeTF, setActiveTF] = useState("1M");
  const [viewMode, setViewMode] = useState<"Single" | "Split">("Single");

  function toggleOverlay(label: string) {
    setActiveToggle(prev => {
      const next = new Set(prev);
      next.has(label) ? next.delete(label) : next.add(label);
      return next;
    });
  }

  return (
    <div className="flex flex-col flex-1 bg-[#131722] border border-[#2A2E39] rounded overflow-hidden">
      {/* OHLC Header */}
      <div className="px-3 py-1.5 border-b border-[#2A2E39] bg-[#1C2030] flex items-center gap-2 text-[11px] font-mono text-gray-300">
        <span className="text-white font-semibold">{symbol}</span>
        <span className="text-gray-500">·</span>
        <span>M1</span>
        <span className="text-gray-500">·</span>
        <span className="text-accent font-medium">Live</span>
        <span className="text-gray-500 ml-1">O</span><span className="text-white">2055.15</span>
        <span className="text-gray-500">H</span><span className="text-white">2056.40</span>
        <span className="text-gray-500">L</span><span className="text-white">2054.04</span>
        <span className="text-gray-500">C</span><span className="text-white">2055.07</span>
      </div>

      {/* Overlay Toggles Row */}
      <div className="px-3 py-2 border-b border-[#2A2E39] bg-[#1E222D] flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-1.5 flex-wrap">
          {TOGGLES.map(t => (
            <button
              key={t.label}
              onClick={() => toggleOverlay(t.label)}
              className={`px-2 py-0.5 text-[11px] font-medium rounded border transition-all ${
                activeToggle.has(t.label)
                  ? t.color
                  : "bg-transparent text-gray-500 border-gray-700 opacity-50"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-1 ml-auto">
          {(["Single", "Split"] as const).map(m => (
            <button
              key={m}
              onClick={() => setViewMode(m)}
              className={`px-2.5 py-0.5 text-[11px] font-medium rounded border transition-all ${
                viewMode === m
                  ? "bg-[#2A2E39] text-white border-[#363B4E]"
                  : "bg-transparent text-gray-500 border-gray-700"
              }`}
            >
              {m}
            </button>
          ))}
          <button className="p-1 rounded text-gray-500 hover:text-white hover:bg-[#2A2E39] transition-colors ml-0.5">
            <Maximize2 className="w-3.5 h-3.5" />
          </button>
          <button className="px-2 py-0.5 text-[11px] text-gray-500 hover:text-white hover:bg-[#2A2E39] rounded border border-transparent hover:border-[#363B4E] transition-colors">
            Clear all
          </button>
        </div>
      </div>

      {/* Timeframe + Chart Body */}
      <div className="flex-1 relative min-h-[400px]" style={{
        backgroundImage: "linear-gradient(#1E222D 1px, transparent 1px), linear-gradient(90deg, #1E222D 1px, transparent 1px)",
        backgroundSize: "60px 50px",
      }}>
        {/* Timeframe selector */}
        <div className="absolute top-2 left-3 flex items-center gap-1 z-10">
          {["1M", "5M", "15M", "1H"].map(tf => (
            <button
              key={tf}
              onClick={() => setActiveTF(tf)}
              className={`px-2.5 py-1 text-xs font-bold rounded transition-all ${
                activeTF === tf
                  ? "bg-blue-600 text-white"
                  : "text-gray-400 hover:text-white hover:bg-[#2A2E39]"
              }`}
            >
              {tf}
            </button>
          ))}
        </div>

        {/* Watermark */}
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none select-none">
          <span className="text-[#2A2E39] text-3xl font-bold opacity-40 tracking-widest">{symbol} TRADINGVIEW MOCKUP</span>
        </div>

        {/* Mock candles — simplified candlestick bars */}
        <div className="absolute bottom-[38%] left-[8%] flex items-end gap-[3px]">
          {[
            { h: 72, bull: false }, { h: 40, bull: true }, { h: 56, bull: true },
            { h: 64, bull: false }, { h: 48, bull: true }, { h: 80, bull: false },
            { h: 36, bull: true }, { h: 52, bull: false }, { h: 68, bull: false },
            { h: 32, bull: true }, { h: 44, bull: true }, { h: 96, bull: false },
            { h: 110, bull: false }, { h: 88, bull: false }, { h: 60, bull: true },
            { h: 52, bull: true }, { h: 76, bull: false }, { h: 64, bull: true },
          ].map((c, i) => (
            <div key={i} className="flex flex-col items-center gap-px">
              <div className={`w-[2px] ${c.bull ? "bg-green-500/60" : "bg-red-500/60"}`} style={{ height: c.h * 0.25 }} />
              <div className={`w-[5px] rounded-[1px] ${c.bull ? "bg-green-500" : "bg-red-500"}`} style={{ height: c.h * 0.55 }} />
              <div className={`w-[2px] ${c.bull ? "bg-green-500/60" : "bg-red-500/60"}`} style={{ height: c.h * 0.2 }} />
            </div>
          ))}
        </div>

        {/* BOS annotation */}
        <div className="absolute bottom-[52%] left-[22%] font-mono text-[9px] text-red-400 border-b border-red-400/50 px-1">BOS</div>

        {/* Mock OB zone */}
        <div className="absolute bottom-[55%] left-[15%] w-36 h-6 bg-red-500/10 border border-red-500/30 flex items-center px-2">
          <span className="text-[9px] text-red-400/60 font-mono">BEARISH OB</span>
        </div>

        {/* Right price axis */}
        <div className="absolute right-0 top-0 bottom-0 w-14 bg-[#131722] border-l border-[#2A2E39] flex flex-col justify-between py-8 font-mono text-[10px] text-gray-500 items-end pr-2 pointer-events-none z-10">
          <span>2120.00</span>
          <span>2100.00</span>
          <span>2080.00</span>
          <span>2060.00</span>
          <span>2040.00</span>
          <span>2020.00</span>
          <span>2000.00</span>
          <span className="text-white bg-[#2A2E39] px-1 py-0.5 rounded -mr-1">1983.49</span>
          <span>1970.00</span>
        </div>
      </div>
    </div>
  );
}

export default function Crypto() {
  const [activeTab, setActiveTab] = useState("chart");

  return (
    <PageLayout>
      <div className="flex flex-col h-full overflow-hidden">

        {/* Sub-tab bar */}
        <div className="flex items-center gap-1 px-4 pt-3 pb-0 border-b border-border bg-card">
          {TABS.map(tab => {
            const Icon = tab.icon;
            const isActive = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`flex items-center gap-1.5 px-4 py-2 text-xs font-semibold rounded-t-md border-b-2 transition-all -mb-px ${
                  isActive
                    ? "border-primary text-primary bg-primary/5"
                    : "border-transparent text-muted-foreground hover:text-foreground hover:bg-secondary/40"
                }`}
              >
                <Icon className="w-3.5 h-3.5" />
                {tab.label}
              </button>
            );
          })}
        </div>

        {/* Page body */}
        <div className="flex-1 flex flex-col p-4 gap-3 overflow-hidden">

          {/* Market status pill */}
          <div className="w-fit">
            <div className="flex items-center gap-2 px-3 py-1 bg-accent/10 border border-accent/25 rounded text-[11px] font-mono text-accent font-medium">
              <span className="w-1.5 h-1.5 rounded-full bg-accent inline-block animate-pulse" />
              CRYPTO — ETH/USDT · Bybit · 24/7 Market — Always Open
            </div>
          </div>

          {/* ETH/USDT header card */}
          <div className="bg-card border border-border rounded-md px-5 py-4 flex items-start justify-between">
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-2">
                <span className="text-blue-400 text-lg">◆</span>
                <h1 className="text-lg font-bold text-foreground tracking-tight">ETH/USDT</h1>
              </div>
              <p className="text-xs text-muted-foreground">Multi-timeframe chart · ICT overlays · Bybit live feed</p>
            </div>
            <div className="flex flex-col items-end gap-1">
              <Badge className="bg-accent text-accent-foreground text-[10px] font-bold tracking-widest px-2 py-0.5 self-end">
                LIVE
              </Badge>
              <span className="font-mono text-3xl font-bold text-foreground leading-none">1983.49</span>
              <span className="font-mono text-xs text-accent font-bold">+0.78 (0.04%)</span>
            </div>
          </div>

          {/* 4 stat boxes */}
          <div className="grid grid-cols-4 bg-card border border-border rounded-md divide-x divide-border">
            {[
              { label: "TREND", value: "—" },
              { label: "RSI (14)", value: "—" },
              { label: "ATR", value: "—" },
              { label: "PRICE", value: "—" },
            ].map(stat => (
              <div key={stat.label} className="flex flex-col px-5 py-3">
                <span className="text-[10px] text-muted-foreground uppercase tracking-widest font-semibold mb-1.5">{stat.label}</span>
                <span className="font-mono text-sm text-foreground">{stat.value}</span>
              </div>
            ))}
          </div>

          {/* Tab content */}
          <div className="flex-1 flex flex-col min-h-0">
            {activeTab === "chart" && <ChartArea symbol="ETHUSDT" />}

            {activeTab === "backtest" && (
              <div className="flex-1 flex items-center justify-center border border-dashed border-border rounded-md">
                <span className="text-muted-foreground font-mono text-sm">[Crypto Backtest Engine UI]</span>
              </div>
            )}

            {activeTab === "signals" && (
              <div className="flex-1 flex items-center justify-center border border-dashed border-border rounded-md">
                <span className="text-muted-foreground font-mono text-sm">[Signal Feed Stream]</span>
              </div>
            )}

            {activeTab === "optimizer" && (
              <div className="flex-1 flex items-center justify-center border border-dashed border-border rounded-md">
                <span className="text-muted-foreground font-mono text-sm">[Parameter Optimizer Grid]</span>
              </div>
            )}
          </div>

        </div>
      </div>
    </PageLayout>
  );
}
