import { PageLayout } from "@/components/layout/PageLayout";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useBotControl, useDecisionLogs } from "@/hooks/use-trading-data";
import { Play, Square, Activity, Wifi, Server, Clock } from "lucide-react";
import { useState } from "react";
import { useToast } from "@/hooks/use-toast";

function StrategyCard({
  title,
  subtitle,
  status,
  metrics,
  pnl,
  winRate,
  onToggle,
}: {
  title: string;
  subtitle: string;
  status: "RUNNING" | "STOPPED";
  metrics: { signals: number; uptime: string };
  pnl: string;
  winRate: string;
  onToggle: (action: "START" | "STOP") => void;
}) {
  const isRunning = status === "RUNNING";
  return (
    <div className="flex flex-col rounded-lg border border-border bg-card overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border/60 bg-secondary/20">
        <div className="flex items-center gap-2">
          <span className="font-bold text-sm tracking-wider">{title}</span>
          {isRunning && (
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-accent opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2 w-2 bg-accent"></span>
            </span>
          )}
        </div>
        <Badge
          variant="secondary"
          className={`text-[10px] font-mono font-bold px-2 py-0.5 ${
            isRunning
              ? "bg-accent/15 text-accent border border-accent/30"
              : "bg-secondary text-muted-foreground border border-border"
          }`}
        >
          {status}
        </Badge>
      </div>

      <div className="px-4 pt-3 pb-1">
        <p className="text-xs text-muted-foreground font-mono">{subtitle}</p>
      </div>

      <div className="grid grid-cols-2 gap-0 px-4 pb-3 pt-2">
        <div className="flex flex-col gap-0.5">
          <span className="text-[10px] text-muted-foreground uppercase font-semibold">Signals Today</span>
          <span className="font-mono text-2xl font-bold">{metrics.signals}</span>
        </div>
        <div className="flex flex-col gap-0.5">
          <span className="text-[10px] text-muted-foreground uppercase font-semibold">Uptime</span>
          <span className="font-mono text-2xl font-bold">{metrics.uptime}</span>
        </div>
      </div>

      <div className="mx-4 mb-3 rounded-md border border-border bg-background/60 grid grid-cols-3 divide-x divide-border text-center py-2">
        <div className="flex flex-col gap-0.5">
          <span className="text-[9px] text-muted-foreground uppercase">Market</span>
          <span className="text-xs font-bold text-accent">OPEN</span>
        </div>
        <div className="flex flex-col gap-0.5">
          <span className="text-[9px] text-muted-foreground uppercase">Regime</span>
          <span className="text-xs font-bold">TREND</span>
        </div>
        <div className="flex flex-col gap-0.5">
          <span className="text-[9px] text-muted-foreground uppercase">Session</span>
          <span className="text-xs font-bold">NY</span>
        </div>
      </div>

      <div className="mx-4 mb-3 grid grid-cols-2 gap-3">
        <div className="flex flex-col gap-0.5">
          <span className="text-[10px] text-muted-foreground uppercase">Today's P&amp;L</span>
          <span className={`font-mono text-sm font-bold ${pnl.startsWith("+") ? "text-accent" : pnl.startsWith("-") ? "text-destructive" : "text-muted-foreground"}`}>{pnl}</span>
        </div>
        <div className="flex flex-col gap-0.5">
          <span className="text-[10px] text-muted-foreground uppercase">Win Rate</span>
          <span className="font-mono text-sm font-bold">{winRate}</span>
        </div>
      </div>

      <div className="px-4 pb-4">
        <Button
          variant={isRunning ? "destructive" : "default"}
          className="w-full font-bold tracking-wider"
          onClick={() => onToggle(isRunning ? "STOP" : "START")}
        >
          {isRunning ? (
            <><Square className="w-4 h-4 mr-2 fill-current" /> STOP BOT</>
          ) : (
            <><Play className="w-4 h-4 mr-2 fill-current" /> START BOT</>
          )}
        </Button>
      </div>
    </div>
  );
}

export default function LiveBot() {
  const [filter, setFilter] = useState("ALL");
  const { data: logs, isLoading } = useDecisionLogs(filter);
  const botControl = useBotControl();
  const { toast } = useToast();

  const handleToggle = (strategy: string, action: "START" | "STOP") => {
    botControl.mutate({ strategy, action }, {
      onSuccess: () => {
        toast({
          title: `Bot ${action === "START" ? "Started" : "Stopped"}`,
          description: `${strategy} is now ${action === "START" ? "running" : "inactive"}.`,
          variant: action === "START" ? "default" : "destructive",
        });
      },
    });
  };

  return (
    <PageLayout>
      {/* System Status Bar */}
      <div className="flex items-center gap-6 px-4 py-2 border-b border-border/60 bg-card/50 text-xs font-mono">
        <div className="flex items-center gap-1.5 text-accent">
          <Wifi className="w-3 h-3" />
          <span className="font-bold">SYSTEM ONLINE</span>
        </div>
        <div className="h-3 w-px bg-border/60" />
        <div className="flex items-center gap-1.5 text-muted-foreground">
          <Server className="w-3 h-3" />
          <span>API <span className="text-accent font-bold">CONNECTED</span></span>
        </div>
        <div className="h-3 w-px bg-border/60" />
        <div className="flex items-center gap-1.5 text-muted-foreground">
          <Clock className="w-3 h-3" />
          <span>NY SESSION <span className="text-foreground font-bold">ACTIVE</span></span>
        </div>
        <div className="h-3 w-px bg-border/60" />
        <div className="flex items-center gap-1.5 text-muted-foreground">
          <span>ETH/USDT</span>
          <span className="text-accent font-bold">+0.04%</span>
          <span className="text-foreground">1983.49</span>
        </div>
        <div className="ml-auto flex items-center gap-1.5 text-muted-foreground">
          <span>BOTS ACTIVE:</span>
          <span className="text-accent font-bold">2 / 3</span>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
        {/* Strategy Cards */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          <StrategyCard
            title="RSI EMA STRATEGY"
            subtitle="XAUUSD · 5M / 15M · Multi-timeframe"
            status="RUNNING"
            metrics={{ signals: 4, uptime: "3d 2h" }}
            pnl="+$420.00"
            winRate="68.5%"
            onToggle={(a) => handleToggle("RSI EMA", a)}
          />
          <StrategyCard
            title="XAU SCALP"
            subtitle="XAUUSD · 1M / 5M · ICT execution"
            status="RUNNING"
            metrics={{ signals: 12, uptime: "1d 5h" }}
            pnl="+$842.50"
            winRate="72.4%"
            onToggle={(a) => handleToggle("XAU SCALP", a)}
          />
          <StrategyCard
            title="ETH RSI 15 TF"
            subtitle="ETH/USDT · 15M · RSI momentum"
            status="STOPPED"
            metrics={{ signals: 0, uptime: "—" }}
            pnl="—"
            winRate="—"
            onToggle={(a) => handleToggle("ETH RSI 15", a)}
          />
        </div>

        {/* Decision Log */}
        <div className="rounded-lg border border-border bg-card overflow-hidden">
          <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between px-4 py-3 border-b border-border bg-secondary/10 gap-3">
            <div className="flex items-center gap-2">
              <Activity className="w-4 h-4 text-muted-foreground" />
              <span className="font-mono font-bold text-sm">SEAN ALGO · decision_log</span>
            </div>
            <div className="flex items-center gap-1 bg-secondary/60 p-1 rounded-lg">
              {["ALL", "RSI EMA", "ETH-RSI", "CLR"].map((f) => (
                <button
                  key={f}
                  onClick={() => setFilter(f)}
                  className={`px-3 py-1 text-xs font-bold uppercase rounded-md transition-all ${
                    filter === f
                      ? "bg-background text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground hover:bg-background/50"
                  }`}
                >
                  {f}
                </button>
              ))}
            </div>
          </div>
          <Table>
            <TableHeader className="bg-secondary/30">
              <TableRow className="border-border hover:bg-transparent">
                <TableHead className="w-[130px] font-mono text-[10px] text-muted-foreground uppercase">Time</TableHead>
                <TableHead className="w-[140px] font-mono text-[10px] text-muted-foreground uppercase">Source</TableHead>
                <TableHead className="w-[130px] font-mono text-[10px] text-muted-foreground uppercase">Signal</TableHead>
                <TableHead className="font-mono text-[10px] text-muted-foreground uppercase">Context</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {isLoading ? (
                <TableRow>
                  <TableCell colSpan={4} className="text-center py-8 text-muted-foreground text-xs">Loading logs...</TableCell>
                </TableRow>
              ) : logs?.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={4} className="text-center py-8 text-muted-foreground text-xs">No logs found for this filter.</TableCell>
                </TableRow>
              ) : (
                logs?.map((log) => (
                  <TableRow key={log.id} className="border-border hover:bg-secondary/20 transition-colors">
                    <TableCell className="font-mono text-xs text-muted-foreground">{log.timestamp}</TableCell>
                    <TableCell>
                      <Badge variant="outline" className="font-mono text-[10px] bg-secondary/50 border-border">
                        {log.source}
                      </Badge>
                    </TableCell>
                    <TableCell
                      className={`font-mono text-xs font-bold ${
                        log.signal.includes("+")
                          ? "text-accent"
                          : log.signal.includes("-")
                          ? "text-destructive"
                          : "text-foreground"
                      }`}
                    >
                      {log.signal}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground max-w-[320px] truncate" title={log.context}>
                      {log.context}
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
      </div>
    </PageLayout>
  );
}
