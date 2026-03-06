import { motion } from "framer-motion";
import { Bot, Sparkles } from "lucide-react";

export default function Sidebar({ items, activeItem, status, onNavigate }) {
  return (
    <aside className="w-full shrink-0 lg:sticky lg:top-5 lg:h-[calc(100vh-2.5rem)] lg:w-[290px]">
      <div className="megaboost-panel flex h-full flex-col p-4">
        <div className="rounded-[24px] border border-accent-red/20 bg-gradient-to-br from-accent-red/22 via-accent-crimson/10 to-transparent p-5 shadow-ember">
          <div className="flex items-center gap-3">
            <div className="flex h-12 w-12 items-center justify-center rounded-2xl border border-accent-red/30 bg-black/20 text-accent-orange">
              <Bot className="h-6 w-6" />
            </div>
            <div>
              <div className="text-[0.7rem] uppercase tracking-[0.34em] text-red-200/70">MegaBoost Theme</div>
              <h1 className="text-2xl font-bold text-white">Trading Engine</h1>
            </div>
          </div>
          <div className="mt-4 flex items-center justify-between rounded-[20px] border border-white/10 bg-black/20 px-4 py-3">
            <div>
              <div className="text-[0.7rem] uppercase tracking-[0.28em] text-zinc-500">Runtime</div>
              <div className="mt-1 text-sm font-semibold text-zinc-100">{status?.botStatus || "UNKNOWN"}</div>
            </div>
            <Sparkles className="h-5 w-5 text-accent-orange" />
          </div>
        </div>

        <nav className="mt-4 flex gap-2 overflow-x-auto pb-2 lg:flex-1 lg:flex-col lg:overflow-visible">
          {items.map((item) => {
            const Icon = item.icon;
            const active = item.id === activeItem;
            return (
              <motion.button
                key={item.id}
                type="button"
                whileHover={{ x: 3 }}
                whileTap={{ scale: 0.98 }}
                onClick={() => onNavigate(item.id)}
                className={`group flex min-w-[220px] items-center gap-3 rounded-[20px] border px-4 py-3 text-left transition lg:min-w-0 ${
                  active
                    ? "border-accent-red/35 bg-gradient-to-r from-accent-red/20 via-accent-crimson/10 to-transparent text-white shadow-ember"
                    : "border-white/6 bg-white/[0.02] text-zinc-300 hover:border-accent-red/20 hover:bg-white/[0.04]"
                }`}
              >
                <div
                  className={`flex h-11 w-11 items-center justify-center rounded-2xl border ${
                    active ? "border-accent-red/30 bg-black/20 text-accent-orange" : "border-white/8 bg-black/20 text-zinc-500"
                  }`}
                >
                  <Icon className="h-5 w-5" />
                </div>
                <div className="min-w-0">
                  <div className="truncate text-sm font-semibold">{item.label}</div>
                  <div className="truncate text-[0.72rem] uppercase tracking-[0.22em] text-zinc-500">{item.description}</div>
                </div>
              </motion.button>
            );
          })}
        </nav>

        <div className="mt-4 hidden rounded-[22px] border border-white/8 bg-black/20 px-4 py-4 lg:block">
          <div className="text-[0.68rem] uppercase tracking-[0.28em] text-zinc-500">Active Pair</div>
          <div className="mt-1 text-lg font-semibold text-white">{status?.pair || "XAUUSD"}</div>
          <div className="mt-3 text-[0.68rem] uppercase tracking-[0.28em] text-zinc-500">Mode</div>
          <div className="mt-1 text-sm font-semibold text-zinc-200">{status?.mode || "BINARY"}</div>
        </div>
      </div>
    </aside>
  );
}
