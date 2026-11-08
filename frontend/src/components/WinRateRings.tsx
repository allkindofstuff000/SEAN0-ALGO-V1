import { useMemo } from "react";
import type { LiveSignal } from "@/lib/api";
import { computeStrategyStats, type StrategyStats } from "@/lib/strategy-stats";

// Theme colours as hex (the app is dark-only; SVG gradient stops need literal colours).
const GREEN = "#12d184";       // accent
const GREEN_DEEP = "#067a4c";
const RED = "#f5465c";         // destructive
const RED_DEEP = "#8f1f2c";
const DISC_LIGHT = "#2a3146";
const DISC_DARK = "#121622";
const TRACK = "#1c2233";

const SHORT_LABEL: Record<string, string> = {
  "rsi-ema": "GOLD RSI",
  "vwap-st": "VWAP+ST",
  "macd-gold": "GOLD MACD",
  "rsi-btc": "BTC RSI",
  "rsi-eth": "ETH RSI",
  "rsi-sol": "SOL RSI",
  "nyorb-eth": "ETH NY",
};

function arcPath(cx: number, cy: number, r: number, startDeg: number, endDeg: number): string {
  // SVG arc from startDeg to endDeg (clockwise, 0° = 12 o'clock). Handles full circles.
  const sweep = Math.min(359.999, Math.max(0, endDeg - startDeg));
  const toXY = (deg: number) => {
    const a = ((deg - 90) * Math.PI) / 180;
    return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  };
  const [x1, y1] = toXY(startDeg);
  const [x2, y2] = toXY(startDeg + sweep);
  const large = sweep > 180 ? 1 : 0;
  return `M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${r} ${r} 0 ${large} 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`;
}

/** The 3D ring itself (SVG): bevelled disc, glossy highlight, green win arc, red loss arc. */
function RingSvg({ st, size, idBase }: { st: StrategyStats; size: number; idBase: string }) {
  const cx = size / 2;
  const cy = size / 2;
  const stroke = Math.max(7, Math.round(size * 0.1));
  const r = size / 2 - stroke / 2 - Math.round(size * 0.07);
  const winDeg = st.resolved ? (st.wins / st.resolved) * 360 : 0;
  const lossDeg = st.resolved ? (st.losses / st.resolved) * 360 : 0;
  const gap = st.wins && st.losses ? 3 : 0;   // small notch between the two arcs
  const pct = st.resolved ? Math.round(st.winRate) : null;
  const good = (pct ?? 0) >= 50;
  const mono = "ui-monospace, SFMono-Regular, Menlo, monospace";

  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={`${st.label} win rate ${pct ?? 0} percent`}>
      <defs>
        <radialGradient id={`${idBase}-disc`} cx="35%" cy="30%" r="80%">
          <stop offset="0%" stopColor={DISC_LIGHT} />
          <stop offset="100%" stopColor={DISC_DARK} />
        </radialGradient>
        <linearGradient id={`${idBase}-win`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#6ff7c0" />
          <stop offset="55%" stopColor={GREEN} />
          <stop offset="100%" stopColor={GREEN_DEEP} />
        </linearGradient>
        <linearGradient id={`${idBase}-loss`} x1="1" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#ff8a99" />
          <stop offset="55%" stopColor={RED} />
          <stop offset="100%" stopColor={RED_DEEP} />
        </linearGradient>
        <linearGradient id={`${idBase}-gloss`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#ffffff" stopOpacity="0.22" />
          <stop offset="100%" stopColor="#ffffff" stopOpacity="0" />
        </linearGradient>
        <filter id={`${idBase}-shadow`} x="-30%" y="-30%" width="160%" height="160%">
          <feDropShadow dx="0" dy="3" stdDeviation="3" floodColor="#000" floodOpacity="0.55" />
        </filter>
        <filter id={`${idBase}-glow`} x="-30%" y="-30%" width="160%" height="160%">
          <feGaussianBlur stdDeviation="2" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>

      {/* bevelled disc */}
      <circle cx={cx} cy={cy} r={r + stroke / 2 + 3} fill={`url(#${idBase}-disc)`} filter={`url(#${idBase}-shadow)`} />
      <circle cx={cx} cy={cy} r={r + stroke / 2 + 3} fill="none" stroke="#000" strokeOpacity="0.35" strokeWidth="1" />
      {/* track */}
      <circle cx={cx} cy={cy} r={r} fill="none" stroke={TRACK} strokeWidth={stroke} />
      {/* inner rim shadow for depth */}
      <circle cx={cx} cy={cy} r={r - stroke / 2 - 1} fill="none" stroke="#000" strokeOpacity="0.45" strokeWidth="2" />

      {/* win arc (from 12 o'clock clockwise) */}
      {winDeg > 0 && (
        <path d={arcPath(cx, cy, r, 0, winDeg - gap)} fill="none" stroke={`url(#${idBase}-win)`} strokeWidth={stroke} strokeLinecap="round" filter={`url(#${idBase}-glow)`} />
      )}
      {/* loss arc (the rest) */}
      {lossDeg > 0 && (
        <path d={arcPath(cx, cy, r, winDeg + gap, 360)} fill="none" stroke={`url(#${idBase}-loss)`} strokeWidth={stroke} strokeLinecap="round" />
      )}

      {/* glossy highlight */}
      <ellipse cx={cx} cy={cy - r * 0.45} rx={r * 0.62} ry={r * 0.32} fill={`url(#${idBase}-gloss)`} />

      {/* centre text */}
      <text x={cx} y={cy + size * 0.02} textAnchor="middle" fontFamily={mono} fontWeight="800" fontSize={size * 0.22} fill={pct == null ? "#7b86a3" : good ? GREEN : RED}>
        {pct == null ? "—" : `${pct}%`}
      </text>
      {size >= 100 && (
        <text x={cx} y={cy + size * 0.135} textAnchor="middle" fontFamily={mono} fontSize={size * 0.075} fontWeight="700" letterSpacing="1.5" fill="#7b86a3">
          {pct == null ? "NO DATA" : "WIN RATE"}
        </text>
      )}
    </svg>
  );
}

function RingCard({ st, size = 132 }: { st: StrategyStats; size?: number }) {
  return (
    <div
      className="group flex flex-col items-center rounded-xl border border-border/70 bg-card px-3 pt-3 pb-3 transition-transform duration-200 hover:-translate-y-0.5 hover:border-border"
      title={`${st.label}: ${st.wins} wins, ${st.losses} losses${st.open ? `, ${st.open} open` : ""}${st.expired ? `, ${st.expired} expired` : ""}`}
    >
      <RingSvg st={st} size={size} idBase={`ring-${st.tag}`} />
      <div className="mt-1 text-center">
        <div className="text-[11px] font-bold tracking-wider uppercase">{st.label}</div>
        <div className="font-mono text-[11px] mt-0.5">
          <span style={{ color: GREEN }}>{st.wins}W</span>
          <span className="text-muted-foreground"> · </span>
          <span style={{ color: RED }}>{st.losses}L</span>
          {st.open ? <span className="text-muted-foreground"> · {st.open} open</span> : null}
        </div>
        <div className={`font-mono text-xs font-bold mt-0.5 ${st.netR > 0 ? "text-accent" : st.netR < 0 ? "text-destructive" : "text-muted-foreground"}`}>
          {st.resolved ? `${st.netR > 0 ? "+" : ""}${st.netR.toFixed(1)}R` : "no closed trades"}
        </div>
      </div>
    </div>
  );
}

/** Compact ring for the Charts toolbar strip. */
function RingMini({ st }: { st: StrategyStats }) {
  return (
    <div
      className="flex shrink-0 flex-col items-center px-1"
      title={`${st.label}: ${st.wins} wins, ${st.losses} losses${st.open ? `, ${st.open} open` : ""} · net ${st.netR > 0 ? "+" : ""}${st.netR.toFixed(1)}R`}
    >
      <RingSvg st={st} size={64} idBase={`mini-${st.tag}`} />
      <div className="mt-0.5 text-[9px] font-bold tracking-wider text-foreground/90">{SHORT_LABEL[st.tag] ?? st.label.toUpperCase()}</div>
      <div className="font-mono text-[9px] leading-tight">
        <span style={{ color: GREEN }}>{st.wins}W</span>
        <span className="text-muted-foreground">/</span>
        <span style={{ color: RED }}>{st.losses}L</span>
        <span className={`ml-1 font-bold ${st.netR > 0 ? "text-accent" : st.netR < 0 ? "text-destructive" : "text-muted-foreground"}`}>
          {st.resolved ? `${st.netR > 0 ? "+" : ""}${st.netR.toFixed(1)}R` : ""}
        </span>
      </div>
    </div>
  );
}

export function WinRateRings({ signals, variant = "full" }: { signals: LiveSignal[]; variant?: "full" | "strip" }) {
  const stats = useMemo(() => computeStrategyStats(signals), [signals]);
  const totals = useMemo(() => {
    const wins = stats.reduce((a, s) => a + s.wins, 0);
    const losses = stats.reduce((a, s) => a + s.losses, 0);
    const netR = stats.reduce((a, s) => a + s.netR, 0);
    const first = stats.map((s) => s.firstAt).filter(Boolean).sort()[0];
    return { wins, losses, netR: Math.round(netR * 10) / 10, first };
  }, [stats]);
  const since = totals.first ? String(totals.first).slice(0, 10) : null;
  const closed = totals.wins + totals.losses;
  const overall = closed ? Math.round((totals.wins / closed) * 100) : null;

  if (variant === "strip") {
    return (
      <div className="flex items-center gap-3 overflow-x-auto no-scrollbar px-4 py-2">
        <div className="shrink-0 pr-3 border-r border-border/60">
          <div className="text-[10px] font-bold tracking-widest uppercase">Win rates</div>
          <div className="font-mono text-[10px] text-muted-foreground leading-tight">live · {closed} closed{since ? ` · since ${since}` : ""}</div>
          <div className="font-mono text-[11px] leading-tight mt-0.5">
            <span className={`font-bold ${(overall ?? 0) >= 50 ? "text-accent" : "text-destructive"}`}>{overall == null ? "—" : `${overall}%`}</span>
            <span className="text-muted-foreground"> all · </span>
            <span className={`font-bold ${totals.netR >= 0 ? "text-accent" : "text-destructive"}`}>{totals.netR >= 0 ? "+" : ""}{totals.netR.toFixed(1)}R</span>
          </div>
        </div>
        {stats.map((st) => (
          <RingMini key={st.tag} st={st} />
        ))}
      </div>
    );
  }

  return (
    <section className="rounded-xl border border-border bg-card/60 p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2 mb-3">
        <div>
          <h2 className="text-xs font-bold tracking-widest uppercase">Strategy Win Rates</h2>
          <p className="text-[11px] text-muted-foreground font-mono">
            Live signals scored by the resolver{since ? ` · since ${since}` : ""} · {closed} closed · green = wins, red = losses
          </p>
        </div>
        <div className="font-mono text-xs">
          <span className="text-muted-foreground">ALL STRATEGIES </span>
          <span className="font-bold" style={{ color: GREEN }}>{totals.wins}W</span>
          <span className="text-muted-foreground"> · </span>
          <span className="font-bold" style={{ color: RED }}>{totals.losses}L</span>
          <span className="text-muted-foreground"> · </span>
          <span className={`font-bold ${totals.netR >= 0 ? "text-accent" : "text-destructive"}`}>{totals.netR >= 0 ? "+" : ""}{totals.netR.toFixed(1)}R</span>
        </div>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-7 gap-3">
        {stats.map((st) => (
          <RingCard key={st.tag} st={st} />
        ))}
      </div>
    </section>
  );
}
