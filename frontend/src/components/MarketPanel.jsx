import { Activity, Clock3, Waves, Waypoints } from "lucide-react";

import { formatTimestamp, sessionStrength, titleize } from "../services/formatters";
import { PanelShell, ValueBadge } from "./ui/PanelShell";

const insights = {
  TRENDING: "Trend continuation bias remains favorable for momentum entries.",
  RANGING: "Compression is active. Favor selective entries and stronger confirmation.",
  BREAKOUT: "Expansion regime detected. Watch for post-break retrace quality.",
};

export default function MarketPanel({ market }) {
  const strength = sessionStrength(market?.session);
  const cards = [
    {
      label: "Market Regime",
      value: titleize(market?.regime || "UNKNOWN"),
      subtext: insights[String(market?.regime || "").toUpperCase()] || "Awaiting regime classification.",
      icon: Activity,
    },
    {
      label: "Volatility State",
      value: titleize(market?.volatilityState || "UNKNOWN"),
      subtext: "Derived from ATR expansion and recent price range behavior.",
      icon: Waves,
    },
    {
      label: "Liquidity Zones",
      value: titleize(market?.liquidityZones || "UNKNOWN"),
      subtext: "Current liquidity map from equal highs/lows and sweep detection.",
      icon: Waypoints,
    },
    {
      label: "Session Strength",
      value: `${strength.score}/100`,
      subtext: `${market?.session || "UNKNOWN"} · ${strength.label}`,
      icon: Clock3,
    },
  ];

  return (
    <PanelShell
      title="Market Intelligence Panel"
      subtitle="Context engine"
      accent="orange"
      action={<ValueBadge label="Session" value={market?.session || "UNKNOWN"} tone="warning" />}
    >
      <div className="grid gap-4 md:grid-cols-2">
        {cards.map((card) => {
          const Icon = card.icon;
          return (
            <div key={card.label} className="rounded-[24px] border border-white/8 bg-black/20 p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="text-[0.68rem] uppercase tracking-[0.24em] text-zinc-500">{card.label}</div>
                  <div className="mt-2 text-2xl font-bold text-white">{card.value}</div>
                </div>
                <div className="flex h-11 w-11 items-center justify-center rounded-2xl border border-accent-orange/20 bg-black/25 text-accent-orange">
                  <Icon className="h-5 w-5" />
                </div>
              </div>
              <p className="mt-3 text-sm leading-6 text-zinc-400">{card.subtext}</p>
            </div>
          );
        })}
      </div>

      <div className="mt-5 rounded-[22px] border border-white/8 bg-black/20 px-4 py-4 text-sm text-zinc-400">
        Last market sync {formatTimestamp(market?.updatedAt, { showSeconds: false })}
      </div>
    </PanelShell>
  );
}
