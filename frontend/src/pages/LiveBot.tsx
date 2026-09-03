import { PageLayout } from "@/components/layout/PageLayout";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useBotControl, useBotStatus, useLivePrice, useCandles, useMarketStatus } from "@/hooks/use-trading-data";
import { SessionSchedule } from "@/components/SessionSchedule";
import { Play, Square, Wifi, Server, Clock } from "lucide-react";
import { useMemo } from "react";
import { useToast } from "@/hooks/use-toast";
import { computeIndicators } from "@/lib/indicators";

function uptimeFrom(startedAt: string | null | undefined): string {
  if (!startedAt) return "—";
  const start = new Date(startedAt).getTime();
  if (Number.isNaN(start)) return "—";
  const secs = Math.max(0, Math.floor((Date.now() - start) / 1000));
  const d = Math.floor(secs / 86400);
  const h = Math.floor((secs % 86400) / 3600);
  const m = Math.floor((secs % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function StrategyCard({
  title, subtitle, running, uptime, pending, onToggle, sessionText,
}: {
  title: string; subtitle: string; running: boolean; uptime: string; pending: boolean;
  onToggle: (action: "START" | "STOP") => void; sessionText?: string;
}) {
  return (
    <div className="flex flex-col rounded-lg border border-border bg-card overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border/60 bg-secondary/20">
        <div className="flex items-center gap-2">
          <span className="font-bold text-sm tracking-wider">{title}</span>
          {running && (
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-accent opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2 w-2 bg-accent"></span>
            </span>
          )}
        </div>
        <Badge variant="secondary" className={`text-[10px] font-mono font-bold px-2 py-0.5 ${running ? "bg-accent/15 text-accent border border-accent/30" : "bg-secondary text-muted-foreground border border-border"}`}>
          {running ? "RUNNING" : "STOPPED"}
        </Badge>
      </div>

      <div className="px-4 pt-3 pb-1">
        <p className="text-xs text-muted-foreground font-mono">{subtitle}</p>
      </div>

      <div className="grid grid-cols-2 gap-0 px-4 pb-3 pt-2">
        <div className="flex flex-col gap-0.5">
          <span className="text-[10px] text-muted-foreground uppercase font-semibold">Status</span>
          <span className={`font-mono text-2xl font-bold ${running ? "text-accent" : "text-muted-foreground"}`}>{running ? "LIVE" : "OFF"}</span>
        </div>
        <div className="flex flex-col gap-0.5">
          <span className="text-[10px] text-muted-foreground uppercase font-semibold">Uptime</span>
          <span className="font-mono text-2xl font-bold">{uptime}</span>
        </div>
      </div>

      <div className="mx-4 mb-3 rounded-md border border-border bg-background/60 py-2 px-3 text-center">
        <span className="text-[9px] text-muted-foreground uppercase">Session</span>
        <span className="block text-xs font-bold mt-0.5">{sessionText || "—"}</span>
      </div>

      <div className="px-4 pb-4">
        <Button variant={running ? "destructive" : "default"} className="w-full font-bold tracking-wider" disabled={pending} onClick={() => onToggle(running ? "STOP" : "START")}>
          {running ? <><Square className="w-4 h-4 mr-2 fill-current" /> STOP BOT</> : <><Play className="w-4 h-4 mr-2 fill-current" /> START BOT</>}
        </Button>
      </div>
    </div>
  );
}

export default function LiveBot() {
  const { toast } = useToast();
  const { data: bot } = useBotStatus();
  const { data: live } = useLivePrice();
  const { data: candleData } = useCandles("M5", 60);
  const { data: market } = useMarketStatus();
  const botControl = useBotControl();

  const ind = useMemo(() => computeIndicators(candleData?.candles ?? []), [candleData]);
  const price = live?.price || ind?.price || 0;
  const sessionText = market?.closed ? (market?.reason || "Market Closed") : "Market Open";

  const handleToggle = (strategy: "rsi-ema" | "vwap-st" | "btc-rsi-ema", label: string, action: "START" | "STOP") => {
    botControl.mutate({ strategy, action }, {
      onSuccess: (r) => toast({ title: `${label} ${action === "START" ? "Started" : "Stopped"}`, description: (r as any).message || "", variant: action === "START" ? "default" : "destructive" }),
      onError: (e: any) => toast({ title: "Action failed", description: String(e.message || e), variant: "destructive" }),
    });
  };

  return (
    <PageLayout>
      {/* System Status Bar */}
      <div className="flex items-center gap-6 px-4 py-2 border-b border-border/60 bg-card/50 text-xs font-mono flex-wrap">
        <div className={`flex items-center gap-1.5 ${bot?.anyRunning ? "text-accent" : "text-muted-foreground"}`}>
          <Wifi className="w-3 h-3" />
          <span className="font-bold">{bot?.anyRunning ? "SYSTEM ONLINE" : "SYSTEM IDLE"}</span>
        </div>
        <div className="h-3 w-px bg-border/60" />
        <div className="flex items-center gap-1.5 text-muted-foreground">
          <Server className="w-3 h-3" />
          <span>API <span className="text-accent font-bold">CONNECTED</span></span>
        </div>
        <div className="h-3 w-px bg-border/60" />
        <div className="flex items-center gap-1.5 text-muted-foreground">
          <Clock className="w-3 h-3" />
          <span>{sessionText.toUpperCase()} <span className={`font-bold ${market?.closed ? "text-destructive" : "text-accent"}`}>{market?.closed ? "CLOSED" : "OPEN"}</span></span>
        </div>
        <div className="h-3 w-px bg-border/60" />
        <div className="flex items-center gap-1.5 text-muted-foreground">
          <span>XAU/USD</span>
          <span className={`font-bold ${(ind?.changePct ?? 0) >= 0 ? "text-accent" : "text-destructive"}`}>{(ind?.changePct ?? 0) >= 0 ? "+" : ""}{(ind?.changePct ?? 0).toFixed(2)}%</span>
          <span className="text-foreground">{price ? price.toFixed(2) : "—"}</span>
        </div>
        <div className="ml-auto flex items-center gap-1.5 text-muted-foreground">
          <span>LIVE STRATEGIES:</span>
          <span className="text-accent font-bold">{[bot?.rsiEma?.running, bot?.vwapSt?.running, bot?.btcRsiEma?.running].filter(Boolean).length} / 3</span>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
        {/* Strategy Cards */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          <StrategyCard
            title="RSI EMA STRATEGY"
            subtitle="XAUUSD · 5M / 15M · Multi-timeframe"
            running={!!bot?.rsiEma?.running}
            uptime={uptimeFrom(bot?.rsiEma?.startedAt)}
            pending={botControl.isPending}
            onToggle={(a) => handleToggle("rsi-ema", "RSI EMA", a)}
            sessionText={sessionText}
          />

          <StrategyCard
            title="VWAP + SUPERTREND"
            subtitle="XAUUSD · M5 · ST(10, 3.0) @ 1:2 RR"
            running={!!bot?.vwapSt?.running}
            uptime={uptimeFrom(bot?.vwapSt?.startedAt)}
            pending={botControl.isPending}
            onToggle={(a) => handleToggle("vwap-st", "VWAP+ST", a)}
            sessionText={sessionText}
          />

          <StrategyCard
            title="BTC RSI EMA"
            subtitle="BTCUSD · 5M / 15M · Binance · 24/7"
            running={!!bot?.btcRsiEma?.running}
            uptime={uptimeFrom(bot?.btcRsiEma?.startedAt)}
            pending={botControl.isPending}
            onToggle={(a) => handleToggle("btc-rsi-ema", "BTC RSI EMA", a)}
            sessionText="24/7 · Crypto"
          />
        </div>

        {/* Trading session schedule (Dhaka time) */}
        <SessionSchedule />
      </div>
    </PageLayout>
  );
}
