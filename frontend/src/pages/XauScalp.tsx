import { PageLayout } from "@/components/layout/PageLayout";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { CheckCircle2, AlertCircle, BarChart2, Settings, History, Zap, Play, Square } from "lucide-react";
import { useMemo, useState } from "react";
import { LiveChart } from "@/components/LiveChart";
import { computeIndicators } from "@/lib/indicators";
import {
  useCandles, useXauStatus, useXauStats, useXauConditions, useXauLog, useXauSignals, useBotControl,
} from "@/hooks/use-trading-data";
import { useToast } from "@/hooks/use-toast";
import type { Condition } from "@/lib/api";

const TABS = [
  { id: "chart", label: "Chart", icon: BarChart2 },
  { id: "setup", label: "Live Setup", icon: Settings },
  { id: "history", label: "Signal History", icon: History },
];

const TF_MAP: Record<string, string> = { "1M": "M1", "5M": "M5", "15M": "M15", "1H": "H1" };

const COND_LABELS: [keyof any, string][] = [
  ["session", "Session"],
  ["bias15m", "15M Structure"],
  ["zone5m", "5M OB/FVG"],
  ["sslSweep", "Liquidity Sweep"],
  ["displacement", "Displacement"],
  ["cisd", "CISD"],
  ["entryFVG", "Entry FVG"],
  ["pdaCheck", "PDA Check"],
];

export default function XauScalp() {
  const { toast } = useToast();
  const [activeTab, setActiveTab] = useState("chart");
  const [activeTF, setActiveTF] = useState("5M");
  const [showEma, setShowEma] = useState(true);

  const tf = TF_MAP[activeTF];
  const { data: candleData } = useCandles(tf, 240);
  const { data: status } = useXauStatus();
  const { data: stats } = useXauStats();
  const { data: conditions } = useXauConditions();
  const { data: logData } = useXauLog(60);
  const { data: signalsData } = useXauSignals(30);
  const botControl = useBotControl();

  const ind = useMemo(() => computeIndicators(candleData?.candles ?? []), [candleData]);
  const livePrice = status?.livePrice?.price || ind?.price || 0;
  const changeAbs = ind?.changeAbs ?? 0;
  const changePct = ind?.changePct ?? 0;
  const running = !!status?.initialized && !status?.paused;

  const condObj = (conditions || status?.conditions) as Record<string, Condition> | undefined;

  const toggleBot = () => {
    botControl.mutate(
      { strategy: "xau-scalp", action: running ? "STOP" : "START" },
      {
        onSuccess: (r) => toast({ title: r.status === "started" ? "XAU Scalp started" : "XAU Scalp stopped", description: r.message }),
        onError: (e: any) => toast({ title: "Action failed", description: String(e.message || e), variant: "destructive" }),
      },
    );
  };

  const statRow = [
    { label: "Session", val: condObj?.session?.zoneName || condObj?.session?.headline || "—", color: condObj?.session?.active ? "text-accent" : "text-muted-foreground" },
    { label: "15M Structure", val: condObj?.bias15m?.headline || "—", color: condObj?.bias15m?.active ? "text-accent" : "text-muted-foreground" },
    { label: "Signals Today", val: String(stats?.totalSignals ?? 0), color: "text-foreground" },
    { label: "Bot", val: running ? "RUNNING" : "PAUSED", color: running ? "text-accent" : "text-destructive" },
  ];

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

      {/* Market Pill */}
      <div className="px-4 py-2 border-b border-border/30 shrink-0">
        <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-accent/10 border border-accent/20 text-xs font-bold text-accent">
          <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
          XAUUSD · {condObj?.session?.headline || "Session"} · {condObj?.session?.detail || "ICT execution model"}
        </div>
      </div>

      {/* Header Card */}
      <div className="mx-4 mt-3 rounded-lg border border-border bg-card px-5 py-3 flex items-center justify-between shrink-0">
        <div>
          <div className="flex items-center gap-2">
            <Zap className="w-4 h-4 text-primary" />
            <span className="font-bold text-lg tracking-tight">XAU/USD</span>
            {running && <Badge className="bg-accent/20 text-accent border-accent/40 text-[10px] font-bold px-2 animate-pulse">LIVE</Badge>}
            <Badge className="bg-primary/20 text-primary border-primary/40 text-[10px] font-bold px-2">SCALP</Badge>
          </div>
          <p className="text-xs text-muted-foreground mt-0.5">1M / 5M ICT execution model · Order Block + FVG targeting</p>
        </div>
        <div className="flex items-center gap-6">
          <Button onClick={toggleBot} disabled={botControl.isPending} variant={running ? "destructive" : "default"} className="font-bold uppercase tracking-wider h-9">
            {running ? <><Square className="w-3.5 h-3.5 mr-1.5 fill-current" />Stop</> : <><Play className="w-3.5 h-3.5 mr-1.5 fill-current" />Start</>}
          </Button>
          <div className="text-right">
            <span className="text-[10px] text-muted-foreground uppercase font-bold block">Candles</span>
            <span className="font-mono text-lg font-bold">{status?.candleCount ?? "—"}</span>
          </div>
          <div className="text-right">
            <div className="font-mono text-2xl font-bold tracking-tight">{livePrice ? livePrice.toFixed(2) : "—"}</div>
            <div className={`font-mono text-sm font-bold ${changeAbs >= 0 ? "text-accent" : "text-destructive"}`}>{changeAbs >= 0 ? "+" : ""}{changeAbs.toFixed(2)} ({changePct.toFixed(2)}%)</div>
          </div>
        </div>
      </div>

      {/* Stats Row */}
      <div className="mx-4 mt-2 grid grid-cols-4 border border-border rounded-lg overflow-hidden shrink-0">
        {statRow.map((s, i) => (
          <div key={s.label} className={`px-4 py-3 flex flex-col gap-1 bg-card ${i > 0 ? "border-l border-border" : ""}`}>
            <span className="text-[10px] text-muted-foreground uppercase font-bold">{s.label}</span>
            <span className={`font-mono text-sm font-bold ${s.color}`}>{s.val}</span>
          </div>
        ))}
      </div>

      <div className="flex-1 mx-4 mt-2 mb-4 min-h-0 overflow-hidden">
        {/* CHART TAB */}
        {activeTab === "chart" && (
          <div className="h-full min-h-[400px] flex flex-col rounded-lg border border-border bg-[#131722] overflow-hidden">
            <div className="flex items-center justify-between px-3 py-2 border-b border-[#2A2E39] bg-[#1E222D] shrink-0 flex-wrap gap-2">
              <div className="flex items-center gap-2 text-xs font-mono text-gray-300">
                <span className="font-bold text-white">XAUUSD</span>
                <span className="text-[#555]">·</span>
                <span className={changePct >= 0 ? "text-accent" : "text-destructive"}>{changePct >= 0 ? "+" : ""}{changePct.toFixed(2)}%</span>
              </div>
              <div className="flex items-center gap-1 flex-wrap">
                {["1M", "5M", "15M", "1H"].map((t) => (
                  <button key={t} onClick={() => setActiveTF(t)} className={`px-2 py-0.5 text-xs font-bold rounded transition-all ${activeTF === t ? "bg-blue-600 text-white" : "text-gray-400 hover:text-white hover:bg-[#2A2E39]"}`}>{t}</button>
                ))}
                <div className="h-3 w-px bg-[#2A2E39] mx-1" />
                <button onClick={() => setShowEma((v) => !v)} className={`px-2 py-0.5 text-xs font-bold rounded ${showEma ? "text-yellow-400" : "text-gray-500 hover:text-gray-300"}`}>EMA 9 / 21</button>
              </div>
            </div>
            <div className="flex-1 min-h-0">
              <LiveChart tf={tf} showEMA={showEma} />
            </div>
          </div>
        )}

        {/* LIVE SETUP TAB */}
        {activeTab === "setup" && (
          <div className="h-full overflow-auto space-y-4 pb-4">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div className="rounded-lg border border-border bg-card p-4">
                <p className="text-xs font-bold uppercase tracking-wider border-b border-border pb-2 mb-3">Strategy Status</p>
                <div className="space-y-3">
                  {[
                    { label: "Today's Signals", val: String(stats?.totalSignals ?? 0), color: "text-foreground" },
                    { label: "Strong Conviction", val: String(stats?.strongSignals ?? 0), color: "text-primary" },
                    { label: "Medium Conviction", val: String(stats?.mediumSignals ?? 0), color: "text-accent" },
                    { label: "Last Signal", val: stats?.lastSignalTime ? new Date(stats.lastSignalTime * 1000).toLocaleTimeString() : "—", color: "text-muted-foreground" },
                  ].map((r) => (
                    <div key={r.label} className="flex justify-between items-center">
                      <span className="text-xs text-muted-foreground uppercase">{r.label}</span>
                      <span className={`font-mono font-bold text-sm ${r.color}`}>{r.val}</span>
                    </div>
                  ))}
                </div>
              </div>

              <div className="col-span-1 md:col-span-2 grid grid-cols-2 lg:grid-cols-4 gap-3">
                {COND_LABELS.map(([key, label]) => {
                  const c = condObj?.[key as string];
                  const active = !!c?.active;
                  const badge = c?.badge || (active ? "PASSED" : "WAITING");
                  return (
                    <div key={label} className="rounded-lg border border-border bg-card p-3 flex flex-col gap-2">
                      <span className="text-[10px] uppercase font-bold text-muted-foreground">{label}</span>
                      <span className="font-mono text-sm font-bold tracking-tight truncate" title={c?.detail}>{c?.headline || "—"}</span>
                      <div className={`flex items-center gap-1 text-[9px] font-bold uppercase px-2 py-1 rounded border ${active ? "bg-accent/10 text-accent border-accent/20" : "bg-secondary text-muted-foreground border-border"}`}>
                        {active ? <CheckCircle2 className="w-3 h-3" /> : <AlertCircle className="w-3 h-3" />}
                        {badge}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* ICT Decisions Matrix (live decision log) */}
            <div className="rounded-lg border border-border bg-card overflow-hidden">
              <div className="px-4 py-3 border-b border-border bg-secondary/20">
                <p className="text-xs font-bold uppercase tracking-wider">ICT Decisions Matrix</p>
              </div>
              <Table>
                <TableHeader>
                  <TableRow className="border-border hover:bg-transparent">
                    <TableHead className="font-mono text-[10px] uppercase text-muted-foreground w-[120px]">Time</TableHead>
                    <TableHead className="font-mono text-[10px] uppercase text-muted-foreground w-[140px]">Decision</TableHead>
                    <TableHead className="font-mono text-[10px] uppercase text-muted-foreground w-[100px]">Price</TableHead>
                    <TableHead className="font-mono text-[10px] uppercase text-muted-foreground">Reason</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(logData?.log?.length ?? 0) === 0 ? (
                    <TableRow><TableCell colSpan={4} className="text-center py-8 text-xs text-muted-foreground">No decisions logged yet.</TableCell></TableRow>
                  ) : (
                    logData!.log.slice(0, 12).map((r, i) => (
                      <TableRow key={i} className="border-border hover:bg-secondary/20">
                        <TableCell className="font-mono text-xs text-muted-foreground">{new Date(r.ts).toLocaleTimeString()}</TableCell>
                        <TableCell><Badge variant="outline" className={`text-[9px] font-mono ${r.decision !== "NO_SIGNAL" ? "text-accent border-accent/30" : ""}`}>{r.decision}</Badge></TableCell>
                        <TableCell className="font-mono text-xs">{r.price?.toFixed?.(2) ?? "—"}</TableCell>
                        <TableCell className="text-xs text-muted-foreground truncate max-w-[360px]" title={r.reason}>{r.reason}</TableCell>
                      </TableRow>
                    ))
                  )}
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
                  <TableHead className="font-mono text-[10px] uppercase text-muted-foreground text-right">Score</TableHead>
                  <TableHead className="font-mono text-[10px] uppercase text-muted-foreground">Strength</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(signalsData?.signals?.length ?? 0) === 0 ? (
                  <TableRow><TableCell colSpan={5} className="text-center py-8 text-xs text-muted-foreground">No signals recorded yet.</TableCell></TableRow>
                ) : (
                  signalsData!.signals.map((s, i) => (
                    <TableRow key={i} className="border-border hover:bg-secondary/20">
                      <TableCell className="font-mono text-xs text-muted-foreground">{s.time || (s.ts ? new Date(s.ts).toLocaleTimeString() : "—")}</TableCell>
                      <TableCell><Badge variant={(s.direction || "").toUpperCase() === "BUY" ? "default" : "destructive"} className="text-[10px] font-bold w-14 justify-center">{s.direction || "—"}</Badge></TableCell>
                      <TableCell className="font-mono text-xs text-right font-bold">{s.price?.toFixed?.(2) ?? "—"}</TableCell>
                      <TableCell className="font-mono text-xs text-right">{s.score ?? "—"}</TableCell>
                      <TableCell className="text-xs text-muted-foreground">{s.strength || "—"}</TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>
        )}
      </div>
    </PageLayout>
  );
}
