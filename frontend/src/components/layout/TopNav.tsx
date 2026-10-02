import { Link, useLocation } from "wouter";
import { Activity } from "lucide-react";
import { clsx } from "clsx";

// Strategies grouped by traded pair so the navbar reads as separate sections:
// XAU / gold strategies on the left, the BTC / crypto strategy in its own
// section on the right next to the Live Bot overview.
const GOLD_ITEMS = [
  { href: "/rsi-ema", label: "RSI EMA" },
  { href: "/vwap-st", label: "VWAP+ST" },
  { href: "/macd-gold", label: "GOLD MACD" },
];
const CRYPTO_ITEMS = [
  { href: "/btc-rsi-ema", label: "BTC RSI EMA" },
];

export function TopNav() {
  const [location] = useLocation();

  const linkClass = (href: string) =>
    clsx(
      "px-3 py-2 text-xs font-semibold uppercase tracking-wider transition-colors whitespace-nowrap",
      location === href
        ? "text-primary border-b-2 border-primary"
        : "text-muted-foreground hover:text-foreground hover:bg-secondary/50 rounded-t-md",
    );

  return (
    <header className="sticky top-0 z-50 w-full border-b border-border bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
      <div className="flex h-14 items-center gap-2 px-4 md:px-6 w-full">
        <div className="flex items-center gap-2 mr-2 md:mr-4 shrink-0">
          <Activity className="h-5 w-5 text-primary" />
          <span className="font-bold tracking-tight text-lg uppercase hidden md:inline-block">
            Sean Algo
          </span>
        </div>

        {/* XAU / gold strategies */}
        <nav className="flex items-center gap-1 flex-1 overflow-x-auto no-scrollbar">
          {GOLD_ITEMS.map((item) => (
            <Link key={item.href} href={item.href} className={linkClass(item.href)}>
              {item.label}
            </Link>
          ))}
        </nav>

        {/* BTC / crypto strategies — separate pair section on the right */}
        <span className="h-6 w-px bg-border shrink-0" aria-hidden="true" />
        <div className="flex items-center gap-1 shrink-0">
          {CRYPTO_ITEMS.map((item) => (
            <Link key={item.href} href={item.href} className={linkClass(item.href)}>
              {item.label}
            </Link>
          ))}
        </div>

        {/* Live bot overview */}
        <span className="h-6 w-px bg-border shrink-0" aria-hidden="true" />
        <div className="shrink-0">
          <Link
            href="/live-bot"
            className={clsx(
              "flex items-center gap-2 px-3 py-1.5 rounded-md border border-border text-xs font-bold uppercase transition-all duration-200",
              location === "/live-bot"
                ? "bg-secondary text-foreground shadow-sm"
                : "bg-background text-muted-foreground hover:bg-secondary/50 hover:text-foreground hover-elevate",
            )}
          >
            LIVE BOT
            <div className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-primary opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2 w-2 bg-primary"></span>
            </div>
          </Link>
        </div>
      </div>
    </header>
  );
}
