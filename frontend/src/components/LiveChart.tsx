import { useEffect, useRef, useState } from "react";
import {
  createChart,
  ColorType,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { Api, openCandleStream, type Candle } from "@/lib/api";
import { ema } from "@/lib/indicators";

type Props = {
  tf: string; // M1 | M5 | M15 | H1
  showEMA?: boolean;
  className?: string;
};

const isValidCandle = (c: any): c is Candle =>
  !!c &&
  Number.isFinite(c.time) &&
  Number.isFinite(c.open) &&
  Number.isFinite(c.high) &&
  Number.isFinite(c.low) &&
  Number.isFinite(c.close);

const toBar = (c: Candle) => ({
  time: c.time as UTCTimestamp,
  open: c.open,
  high: c.high,
  low: c.low,
  close: c.close,
});

// Sanitise + sort ascending + dedupe by time (lightweight-charts requirements)
const toBars = (arr: any[]) => {
  const valid = (arr || []).filter(isValidCandle).sort((a, b) => a.time - b.time);
  const out: ReturnType<typeof toBar>[] = [];
  let lastT = -1;
  for (const c of valid) {
    if (c.time === lastT) out[out.length - 1] = toBar(c);
    else out.push(toBar(c));
    lastT = c.time;
  }
  return out;
};

const emaLine = (candles: Candle[], period: number) => {
  const clean = (candles || []).filter(isValidCandle).sort((a, b) => a.time - b.time);
  const vals = ema(clean.map((c) => c.close), period);
  return clean.map((c, i) => ({ time: c.time as UTCTimestamp, value: vals[i] }));
};

export function LiveChart({ tf, showEMA = true, className }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const ema9Ref = useRef<ISeriesApi<"Line"> | null>(null);
  const ema21Ref = useRef<ISeriesApi<"Line"> | null>(null);
  const candlesRef = useRef<Candle[]>([]);
  const [ohlc, setOhlc] = useState<string>("");
  const [status, setStatus] = useState<"loading" | "live" | "error">("loading");

  // Build chart once
  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "#131722" },
        textColor: "#9ca3af",
        fontFamily: "'JetBrains Mono', monospace",
      },
      grid: {
        vertLines: { color: "#1E222D" },
        horzLines: { color: "#1E222D" },
      },
      rightPriceScale: { borderColor: "#2A2E39" },
      timeScale: { borderColor: "#2A2E39", timeVisible: true, secondsVisible: false },
      crosshair: { mode: 1 },
      autoSize: true,
    });
    const candleSeries = chart.addCandlestickSeries({
      upColor: "#26a69a",
      downColor: "#ef5350",
      borderUpColor: "#26a69a",
      borderDownColor: "#ef5350",
      wickUpColor: "#26a69a",
      wickDownColor: "#ef5350",
    });
    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    if (showEMA) {
      ema9Ref.current = chart.addLineSeries({ color: "#F7A600", lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
      ema21Ref.current = chart.addLineSeries({ color: "#3B82F6", lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    }
    chart.subscribeCrosshairMove((p) => {
      if (!p.seriesData || !candleSeriesRef.current) return;
      const d: any = p.seriesData.get(candleSeriesRef.current);
      if (d && d.open != null)
        setOhlc(`O ${d.open.toFixed(2)}  H ${d.high.toFixed(2)}  L ${d.low.toFixed(2)}  C ${d.close.toFixed(2)}`);
    });
    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, [showEMA]);

  // Load data + live stream when tf changes
  useEffect(() => {
    let es: EventSource | null = null;
    let cancelled = false;
    setStatus("loading");

    const applyEma = () => {
      if (!showEMA) return;
      ema9Ref.current?.setData(emaLine(candlesRef.current, 9));
      ema21Ref.current?.setData(emaLine(candlesRef.current, 21));
    };

    Api.candles(tf, 240)
      .then((res) => {
        if (cancelled || !candleSeriesRef.current) return;
        const candles = (res.candles || []).filter(isValidCandle);
        candlesRef.current = candles;
        candleSeriesRef.current.setData(toBars(candles));
        applyEma();
        chartRef.current?.timeScale().fitContent();
        setStatus("live");

        // live updates
        es = openCandleStream(
          tf,
          (e) => {
            if (!candleSeriesRef.current) return;
            try {
              if (e.type === "init" && e.candles) {
                const arr = e.candles[tf] || Object.values(e.candles)[0];
                if (Array.isArray(arr) && arr.length) {
                  const clean = arr.filter(isValidCandle);
                  if (clean.length) {
                    candlesRef.current = clean;
                    candleSeriesRef.current.setData(toBars(clean));
                    applyEma();
                  }
                }
                return;
              }
              let c: any;
              if (e.type === "tick" && e.candles) c = e.candles[tf] ?? Object.values(e.candles)[0];
              else if (e.type === "candle") c = (e as any).candle;
              if (!isValidCandle(c)) return;
              candleSeriesRef.current.update(toBar(c));
              const arr = candlesRef.current;
              if (arr.length && arr[arr.length - 1].time === c.time) arr[arr.length - 1] = c;
              else if (!arr.length || c.time > arr[arr.length - 1].time) arr.push(c);
              applyEma();
            } catch {
              /* ignore bad frame */
            }
          },
          () => setStatus("error"),
        );
      })
      .catch(() => !cancelled && setStatus("error"));

    return () => {
      cancelled = true;
      es?.close();
    };
  }, [tf, showEMA]);

  return (
    <div className={`relative h-full w-full ${className || ""}`}>
      <div ref={containerRef} className="absolute inset-0" />
      <div className="absolute left-2 top-2 z-10 font-mono text-[11px] text-gray-400 pointer-events-none">{ohlc}</div>
      {status !== "live" && (
        <div className="absolute right-2 top-2 z-10 font-mono text-[10px] px-2 py-0.5 rounded bg-black/40">
          {status === "loading" ? "loading…" : "stream offline"}
        </div>
      )}
    </div>
  );
}
