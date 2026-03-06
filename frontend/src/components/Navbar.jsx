import { motion } from "framer-motion";
import { Activity, LogOut, RefreshCcw, ShieldCheck, UserRound } from "lucide-react";

import { formatTimestamp } from "../services/formatters";

const toneClasses = {
  ACTIVE: "border-emerald-400/25 bg-emerald-500/10 text-emerald-100",
  RUNNING: "border-emerald-400/25 bg-emerald-500/10 text-emerald-100",
  ONLINE: "border-emerald-400/25 bg-emerald-500/10 text-emerald-100",
  STALE: "border-amber-400/25 bg-amber-500/10 text-amber-100",
  STOPPED: "border-red-400/25 bg-red-500/10 text-red-100",
  DISCONNECTED: "border-red-400/25 bg-red-500/10 text-red-100",
};

function StatusChip({ label, value }) {
  const normalized = String(value || "UNKNOWN").toUpperCase();
  const classes = toneClasses[normalized] || "border-white/10 bg-white/[0.03] text-zinc-200";

  return (
    <div className={`rounded-full border px-4 py-2 ${classes}`}>
      <div className="text-[0.62rem] uppercase tracking-[0.24em] text-zinc-500">{label}</div>
      <div className="mt-1 text-sm font-semibold text-white">{value || "UNKNOWN"}</div>
    </div>
  );
}

export default function Navbar({ activeLabel, loading, status, onRefresh, onLogout }) {
  return (
    <header className="megaboost-panel sticky top-3 z-20 px-5 py-4">
      <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
        <div>
          <div className="text-[0.68rem] uppercase tracking-[0.34em] text-red-200/70">MegaBoost Trading Engine</div>
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <h2 className="text-3xl font-bold text-white">{activeLabel}</h2>
            <div className="rounded-full border border-white/10 bg-black/25 px-4 py-2 text-xs uppercase tracking-[0.24em] text-zinc-400">
              Pair {status?.pair || "XAUUSD"} · Mode {status?.mode || "BINARY"}
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <StatusChip label="Bot Status" value={status?.botStatus || "UNKNOWN"} />
          <StatusChip label="Connection" value={status?.connectionStatus || "UNKNOWN"} />

          <div className="rounded-full border border-white/10 bg-black/25 px-4 py-3">
            <div className="flex items-center gap-2 text-[0.68rem] uppercase tracking-[0.24em] text-zinc-500">
              <ShieldCheck className="h-3.5 w-3.5 text-accent-orange" />
              Active License
            </div>
            <div className="mt-1 text-sm font-semibold text-white">Valid</div>
          </div>

          <div className="rounded-full border border-white/10 bg-black/25 px-4 py-3">
            <div className="flex items-center gap-2 text-[0.68rem] uppercase tracking-[0.24em] text-zinc-500">
              <UserRound className="h-3.5 w-3.5 text-accent-orange" />
              User
            </div>
            <div className="mt-1 text-sm font-semibold text-white">admin</div>
          </div>

          <motion.button type="button" whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }} className="megaboost-button-muted" onClick={() => onRefresh()}>
            <RefreshCcw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </motion.button>
          <motion.button type="button" whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }} className="megaboost-button-muted" onClick={onLogout}>
            <LogOut className="h-4 w-4" />
            Logout
          </motion.button>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-white/8 pt-4 text-xs uppercase tracking-[0.24em] text-zinc-500">
        <div className="flex items-center gap-2">
          <Activity className="h-4 w-4 text-accent-red" />
          Last heartbeat {formatTimestamp(status?.updatedAt, { showSeconds: false })}
        </div>
        <div>Last signal {formatTimestamp(status?.lastSignalTimestamp, { showSeconds: false })}</div>
      </div>
    </header>
  );
}
