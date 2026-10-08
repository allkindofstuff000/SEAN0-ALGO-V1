import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { PageLayout } from "@/components/layout/PageLayout";
import { LiveChart } from "@/components/LiveChart";
import { useMarketStatus } from "@/hooks/use-trading-data";
import {
  Api,
  CRYPTO_SYMBOLS,
  openCandleStream,
  openCryptoCandleStream,
  type Candle,
  type CryptoKey,
  type StreamEvent,
} from "@/lib/api";
import { LayoutGrid, Square, Activity } from "lucide-react";

// ── One place for every live chart: gold (OANDA stream) + BTC / ETH / SOL ──
// (Coinbase display feed). Pick one symbol, or show all of them at once.

type SymbolKey = "XAU" | CryptoKey;

type SymbolSpec = {
  key: SymbolKey;
  label: string; // ticker shown on the chart
  name: string; // feed description
  candlesFn: (tf: string, count: number) => Promise<{ candles: Candle[] }>;
  streamFn: (tf: string, onEvent: (e: StreamEvent) => void, onError?: (e: Event) => void) => EventSource;
  priceFn: () => Promise<{ price: number }>;
};

const SYMBOLS: SymbolSpec[] = [
  {
    key: "XAU",
    label: "XAUUSD",
    name: "Gold · OANDA live stream",
    candlesFn: (t, c) => Api.candles(t, c),
    streamFn: openCandleStream,
    priceFn: Api.livePrice,
  },
  ...CRYPTO_SYMBOLS.map<SymbolSpec>((s) => ({
    key: s.key,
    label: s.display,
    name: `${s.name} · Coinbase live`,
    candlesFn: (t, c) => Api.cryptoCandles(s.key, t, c),
    streamFn: (tf, onEvent, onError) => openCryptoCandleStream(s.key, tf, onEvent, onError),
    priceFn: () => Api.cryptoLivePrice(s.key),
  })),
];

const TFS = ["M1", "M5", "M15", "H1"] as const;
type Tf = (typeof TFS)[number];
type Layout = "single" | "grid";

// Per-viewer conveniences only (last symbol / timeframe / layout); never state that matters.
function readPref<T extends string>(key: string, fallback: T, allowed?: readonly string[]): T {
  try {
    const v = localStorage.getItem(key);
    if (v && (!allowed || allowed.includes(v))) return v as T;
  } catch {
    /* storage unavailable */
  }
  return fallback;
}
function writePref(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* ignore */
  }
}

function useSymbolPrice(spec: SymbolSpec) {
  return useQuery({
    queryKey: ["chart-price", spec.key],
    queryFn: spec.priceFn,
    refetchInterval: 3_000,
  });
}

function fmtPrice(p: number | undefined) {
  if (!p || !Number.isFinite(p)) return "—";
  return p >= 1000 ? p.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : p.toFixed(2);
}

// Watchlist tile: ticker + live price. Click to focus that symbol.
function WatchTile({ spec, active, onClick }: { spec: SymbolSpec; active: boolean; onClick: () => void }) {
  const { data, isError } = useSymbolPrice(spec);
  return (
    <button
      onClick={onClick}
      className={`flex items-center justify-between gap-3 rounded-md border px-3 py-2 text-left transition-colors min-w-[150px] ${
        active ? "border-primary bg-primary/10" : "border-border bg-card hover:bg-secondary/40"
      }`}
    >
      <div className="flex flex-col">
        <span className="text-xs font-bold tracking-wider">{spec.label}</span>
        <span className="text-[10px] text-muted-foreground">{spec.name.split(" · ")[0]}</span>
      </div>
      <span className={`font-mono text-sm font-bold ${isError ? "text-destructive" : "text-foreground"}`}>
        {isError ? "offline" : fmtPrice(data?.price)}
      </span>
    </button>
  );
}

// One chart panel: header strip (ticker, feed, live price) + the candle chart.
function ChartPanel({
  spec,
  tf,
  showEma,
  compact,
  marketNote,
}: {
  spec: SymbolSpec;
  tf: Tf;
  showEma: boolean;
  compact?: boolean;
  marketNote?: string | null;
}) {
  const { data, isError } = useSymbolPrice(spec);
  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden rounded-lg border border-border bg-[#131722]">
      <div className="flex shrink-0 items-center gap-3 border-b border-[#2A2E39] bg-[#1E222D] px-3 py-1.5">
        <span className="text-sm font-bold text-white">{spec.label}</span>
        <span className="hidden text-[10px] text-gray-400 sm:inline">{spec.name}</span>
        <span className="text-[10px] font-bold text-gray-500">{tf}</span>
        {marketNote && <span className="rounded bg-yellow-500/15 px-1.5 py-0.5 text-[10px] font-bold text-yellow-400">{marketNote}</span>}
        <span className={`ml-auto font-mono font-bold ${compact ? "text-sm" : "text-lg"} ${isError ? "text-destructive" : "text-white"}`}>
          {isError ? "feed offline" : fmtPrice(data?.price)}
        </span>
      </div>
      <div className="min-h-0 flex-1">
        {/* key = symbol + tf so switching symbol remounts the chart with the right feed */}
        <LiveChart key={`${spec.key}-${tf}`} tf={tf} showEMA={showEma} candlesFn={spec.candlesFn} streamFn={spec.streamFn} />
      </div>
    </div>
  );
}

export default function Charts() {
  const [symbol, setSymbol] = useState<SymbolKey>(() => readPref("charts.symbol", "XAU", SYMBOLS.map((s) => s.key)));
  const [tf, setTf] = useState<Tf>(() => readPref("charts.tf", "M5", TFS));
  const [layout, setLayout] = useState<Layout>(() => readPref("charts.layout", "single", ["single", "grid"]));
  const [showEma, setShowEma] = useState<boolean>(() => readPref("charts.ema", "1", ["0", "1"]) === "1");
  const { data: market } = useMarketStatus();

  useEffect(() => writePref("charts.symbol", symbol), [symbol]);
  useEffect(() => writePref("charts.tf", tf), [tf]);
  useEffect(() => writePref("charts.layout", layout), [layout]);
  useEffect(() => writePref("charts.ema", showEma ? "1" : "0"), [showEma]);

  const current = SYMBOLS.find((s) => s.key === symbol) ?? SYMBOLS[0];
  const goldNote = market && market.closed ? (market.reason?.toLowerCase().includes("settlement") ? "settlement break" : "market closed") : null;

  return (
    <PageLayout>
      {/* Toolbar: symbol · timeframe · overlays · layout */}
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border/60 bg-card/50 px-4 py-2">
        <div className="flex items-center gap-1">
          {SYMBOLS.map((s) => (
            <button
              key={s.key}
              onClick={() => {
                setSymbol(s.key);
                setLayout("single");
              }}
              className={`rounded px-2.5 py-1 text-xs font-bold tracking-wider transition-colors ${
                symbol === s.key && layout === "single" ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground"
              }`}
            >
              {s.label}
            </button>
          ))}
        </div>
        <span className="h-5 w-px bg-border" aria-hidden="true" />
        <div className="flex items-center gap-1">
          {TFS.map((t) => (
            <button
              key={t}
              onClick={() => setTf(t)}
              className={`rounded px-2 py-1 text-xs font-bold transition-colors ${
                tf === t ? "bg-blue-600 text-white" : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground"
              }`}
            >
              {t}
            </button>
          ))}
        </div>
        <span className="h-5 w-px bg-border" aria-hidden="true" />
        <button
          onClick={() => setShowEma((v) => !v)}
          className={`rounded px-2 py-1 text-xs font-bold transition-colors ${showEma ? "text-yellow-400" : "text-muted-foreground hover:text-foreground"}`}
        >
          EMA 9 / 21
        </button>
        <div className="ml-auto flex items-center gap-1">
          <button
            onClick={() => setLayout("single")}
            title="One chart"
            className={`flex items-center gap-1 rounded px-2 py-1 text-xs font-bold transition-colors ${layout === "single" ? "bg-secondary text-foreground" : "text-muted-foreground hover:text-foreground"}`}
          >
            <Square className="h-3.5 w-3.5" /> Single
          </button>
          <button
            onClick={() => setLayout("grid")}
            title="All four charts at once"
            className={`flex items-center gap-1 rounded px-2 py-1 text-xs font-bold transition-colors ${layout === "grid" ? "bg-secondary text-foreground" : "text-muted-foreground hover:text-foreground"}`}
          >
            <LayoutGrid className="h-3.5 w-3.5" /> All charts
          </button>
        </div>
      </div>

      {/* Watchlist strip with live prices */}
      <div className="flex shrink-0 gap-2 overflow-x-auto border-b border-border/30 px-4 py-2 no-scrollbar">
        {SYMBOLS.map((s) => (
          <WatchTile
            key={s.key}
            spec={s}
            active={layout === "single" && s.key === symbol}
            onClick={() => {
              setSymbol(s.key);
              setLayout("single");
            }}
          />
        ))}
        <div className="ml-auto hidden items-center gap-1.5 self-center text-[10px] text-muted-foreground lg:flex">
          <Activity className="h-3 w-3 text-primary" />
          gold via OANDA stream · crypto via Coinbase, 2s refresh
        </div>
      </div>

      {/* Charts */}
      <div className="mx-4 my-3 min-h-0 flex-1">
        {layout === "single" ? (
          <div className="h-full min-h-[480px]">
            <ChartPanel spec={current} tf={tf} showEma={showEma} marketNote={current.key === "XAU" ? goldNote : null} />
          </div>
        ) : (
          <div className="grid h-full min-h-[560px] grid-cols-1 gap-2 md:grid-cols-2 md:grid-rows-2">
            {SYMBOLS.map((s) => (
              <div key={s.key} className="min-h-[260px]">
                <ChartPanel spec={s} tf={tf} showEma={showEma} compact marketNote={s.key === "XAU" ? goldNote : null} />
              </div>
            ))}
          </div>
        )}
      </div>
    </PageLayout>
  );
}
