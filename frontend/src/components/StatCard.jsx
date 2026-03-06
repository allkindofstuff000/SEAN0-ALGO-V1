import { motion } from "framer-motion";

const toneStyles = {
  red: "from-accent-red/22 via-accent-crimson/10 to-transparent border-accent-red/18",
  orange: "from-accent-orange/20 via-accent-ember/10 to-transparent border-accent-orange/18",
  neutral: "from-white/8 via-white/[0.03] to-transparent border-white/8",
  emerald: "from-emerald-500/18 via-emerald-500/6 to-transparent border-emerald-400/16",
};

export default function StatCard({ icon: Icon, label, value, subtext, tone = "red" }) {
  return (
    <motion.article
      whileHover={{ y: -4, scale: 1.01 }}
      transition={{ duration: 0.18 }}
      className={`megaboost-panel bg-gradient-to-br ${toneStyles[tone] || toneStyles.red} px-5 py-5`}
    >
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="text-[0.68rem] uppercase tracking-[0.28em] text-zinc-500">{label}</div>
          <div className="mt-3 text-3xl font-bold text-white">{value}</div>
          <div className="mt-2 text-sm text-zinc-400">{subtext}</div>
        </div>
        <div className="flex h-12 w-12 items-center justify-center rounded-2xl border border-accent-red/20 bg-black/20 text-accent-orange shadow-ember">
          {Icon ? <Icon className="h-5 w-5" /> : null}
        </div>
      </div>
    </motion.article>
  );
}
