import { motion } from "framer-motion";
import { Play, RotateCcw, Square, TriangleAlert } from "lucide-react";

import { formatTimestamp } from "../services/formatters";
import { DataRow, PanelShell, ValueBadge } from "./ui/PanelShell";

const controls = [
  { id: "start", label: "Start Bot", icon: Play, className: "megaboost-button" },
  { id: "stop", label: "Stop Bot", icon: Square, className: "megaboost-button-muted" },
  { id: "restart", label: "Restart Bot", icon: RotateCcw, className: "megaboost-button-muted" },
  { id: "emergency_stop", label: "Emergency Stop", icon: TriangleAlert, className: "megaboost-button-danger" },
];

export default function BotControls({ status, activeSignals = 0, pendingAction, onAction }) {
  return (
    <PanelShell
      title="Bot Control Panel"
      subtitle="Execution runtime"
      accent="red"
      action={<ValueBadge label="Bot" value={status?.botStatus || "UNKNOWN"} tone={status?.enabled ? "success" : "danger"} />}
    >
      <div className="grid gap-3 sm:grid-cols-2">
        {controls.map((control) => {
          const Icon = control.icon;
          const busy = pendingAction === control.id;
          return (
            <motion.button
              key={control.id}
              type="button"
              whileHover={{ scale: 1.01 }}
              whileTap={{ scale: 0.98 }}
              disabled={Boolean(pendingAction)}
              className={`${control.className} ${pendingAction ? "cursor-not-allowed opacity-70" : ""}`}
              onClick={() => onAction(control.id)}
            >
              <Icon className={`h-4 w-4 ${busy ? "animate-spin" : ""}`} />
              {busy ? `${control.label}...` : control.label}
            </motion.button>
          );
        })}
      </div>

      <div className="mt-5 grid gap-4 md:grid-cols-3">
        <div className="rounded-[24px] border border-white/8 bg-black/20 p-4">
          <div className="text-[0.68rem] uppercase tracking-[0.24em] text-zinc-500">Bot Status</div>
          <div className="mt-2 text-2xl font-bold text-white">{status?.botStatus || "UNKNOWN"}</div>
        </div>
        <div className="rounded-[24px] border border-white/8 bg-black/20 p-4">
          <div className="text-[0.68rem] uppercase tracking-[0.24em] text-zinc-500">Last Signal Time</div>
          <div className="mt-2 text-lg font-semibold text-white">{formatTimestamp(status?.lastSignalTimestamp, { showSeconds: false })}</div>
        </div>
        <div className="rounded-[24px] border border-white/8 bg-black/20 p-4">
          <div className="text-[0.68rem] uppercase tracking-[0.24em] text-zinc-500">Active Signals</div>
          <div className="mt-2 text-2xl font-bold text-white">{activeSignals}</div>
        </div>
      </div>

      <div className="mt-5">
        <DataRow label="Connection" value={status?.connectionStatus || "UNKNOWN"} hint="API heartbeat from trading engine" />
        <DataRow label="Trading mode" value={status?.mode || "BINARY"} hint="Router output mode" />
      </div>
    </PanelShell>
  );
}
