import { motion } from "framer-motion";

const ACCENT_STYLES = {
  red: "before:bg-gradient-to-r before:from-accent-red/0 before:via-accent-red/30 before:to-accent-red/0",
  orange: "before:bg-gradient-to-r before:from-accent-orange/0 before:via-accent-orange/25 before:to-accent-orange/0",
  neutral: "before:bg-gradient-to-r before:from-white/0 before:via-white/10 before:to-white/0",
  success: "before:bg-gradient-to-r before:from-emerald-400/0 before:via-emerald-400/20 before:to-emerald-400/0",
};

const BADGE_STYLES = {
  red: "border-accent-red/22 bg-accent-red/10 text-red-100",
  orange: "border-accent-orange/22 bg-accent-orange/10 text-orange-100",
  neutral: "border-white/10 bg-white/[0.03] text-zinc-200",
  success: "border-emerald-400/22 bg-emerald-500/10 text-emerald-100",
  warning: "border-yellow-400/22 bg-yellow-500/10 text-yellow-100",
  danger: "border-accent-red/22 bg-accent-red/10 text-red-100",
};

export function PanelShell({ title, subtitle, accent = "red", action, className = "", children, footer }) {
  return (
    <motion.section
      whileHover={{ y: -2 }}
      transition={{ duration: 0.18 }}
      className={`megaboost-panel before:absolute before:left-0 before:right-0 before:top-0 before:h-px ${ACCENT_STYLES[accent] || ACCENT_STYLES.red} ${className}`}
    >
      <div className="relative flex items-start justify-between gap-4 border-b border-white/8 px-5 py-4">
        <div>
          <p className="text-[0.68rem] uppercase tracking-[0.34em] text-zinc-500">{subtitle}</p>
          <h2 className="mt-2 text-2xl font-bold text-white">{title}</h2>
        </div>
        {action ? <div className="shrink-0">{action}</div> : null}
      </div>
      <div className="relative px-5 py-5">{children}</div>
      {footer ? <div className="relative border-t border-white/8 px-5 py-4">{footer}</div> : null}
    </motion.section>
  );
}

export function ValueBadge({ label, value, tone = "neutral" }) {
  return (
    <div className={`rounded-full border px-3 py-2 ${BADGE_STYLES[tone] || BADGE_STYLES.neutral}`}>
      <div className="text-[0.62rem] uppercase tracking-[0.24em] text-zinc-500">{label}</div>
      <div className="mt-1 text-sm font-semibold text-white">{value}</div>
    </div>
  );
}

export function DataRow({ label, value, hint, valueClassName = "" }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-white/6 py-3 last:border-b-0 last:pb-0 first:pt-0">
      <div className="min-w-0">
        <div className="text-[0.68rem] uppercase tracking-[0.24em] text-zinc-500">{label}</div>
        {hint ? <div className="mt-1 text-xs text-zinc-400">{hint}</div> : null}
      </div>
      <div className={`text-right text-sm font-semibold text-white ${valueClassName}`}>{value}</div>
    </div>
  );
}
