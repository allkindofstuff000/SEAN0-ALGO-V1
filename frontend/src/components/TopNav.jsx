import { formatTimestamp } from "../services/formatters";
import { ValueBadge } from "./ui/PanelShell";

const statusTone = (value) => {
  switch (String(value).toUpperCase()) {
    case "RUNNING":
    case "ONLINE":
      return "emerald";
    case "STALE":
    case "IDLE":
      return "amber";
    case "ERROR":
    case "STOPPED":
    case "DISCONNECTED":
      return "rose";
    default:
      return "slate";
  }
};

export default function TopNav({ status }) {
  return (
    <header className="sticky top-0 z-20 border-b border-white/8 bg-ink-950/85 backdrop-blur-xl">
      <div className="mx-auto flex max-w-[1800px] flex-col gap-4 px-4 py-4 lg:flex-row lg:items-center lg:justify-between lg:px-6 xl:px-8">
        <div>
          <p className="font-display text-xs uppercase tracking-[0.35em] text-cyan-300">SEAN0 Quant Control</p>
          <h1 className="mt-2 font-display text-2xl font-semibold text-white lg:text-3xl">Trading Engine Dashboard</h1>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <ValueBadge label="Bot" value={status?.botStatus || "UNKNOWN"} tone={statusTone(status?.botStatus)} />
          <ValueBadge label="Connection" value={status?.connectionStatus || "DISCONNECTED"} tone={statusTone(status?.connectionStatus)} />
          <ValueBadge label="Pair" value={status?.pair || "--"} tone="blue" />
          <ValueBadge label="Mode" value={status?.mode || "--"} tone="violet" />
          <div className="rounded-full border border-white/10 bg-white/[0.03] px-4 py-3 text-right text-xs text-slate-400">
            <div className="uppercase tracking-[0.25em]">Heartbeat</div>
            <div className="mt-1 font-display text-sm text-white">{formatTimestamp(status?.updatedAt)}</div>
          </div>
        </div>
      </div>
    </header>
  );
}
