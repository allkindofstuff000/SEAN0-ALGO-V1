import { Link, useLocation } from "wouter";
import { Activity, Circle } from "lucide-react";
import { clsx } from "clsx";

export function TopNav() {
  const [location] = useLocation();

  const navItems = [
    { href: "/rsi-ema", label: "RSI EMA" },
    { href: "/xau-scalp", label: "XAU SCALP" },
  ];

  return (
    <header className="sticky top-0 z-50 w-full border-b border-border bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
      <div className="flex h-14 items-center px-4 md:px-6 w-full">
        <div className="flex items-center gap-2 mr-8">
          <Activity className="h-5 w-5 text-primary" />
          <span className="font-bold tracking-tight text-lg uppercase hidden md:inline-block">
            Sean Algo
          </span>
        </div>
        
        <nav className="flex items-center space-x-1 flex-1 overflow-x-auto no-scrollbar">
          {navItems.map((item) => {
            const isActive = location === item.href;
            return (
              <Link 
                key={item.href} 
                href={item.href}
                className={clsx(
                  "px-3 py-2 text-xs font-semibold uppercase tracking-wider transition-colors whitespace-nowrap",
                  isActive 
                    ? "text-primary border-b-2 border-primary" 
                    : "text-muted-foreground hover:text-foreground hover:bg-secondary/50 rounded-t-md"
                )}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="ml-auto flex items-center">
          <Link 
            href="/live-bot"
            className={clsx(
              "flex items-center gap-2 px-3 py-1.5 rounded-md border border-border text-xs font-bold uppercase transition-all duration-200",
              location === "/live-bot" 
                ? "bg-secondary text-foreground shadow-sm" 
                : "bg-background text-muted-foreground hover:bg-secondary/50 hover:text-foreground hover-elevate"
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
