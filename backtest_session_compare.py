"""
Session filter comparison backtest
Compares 3 session windows over last 3 months:
  A) Current    — London-NY Overlap + New York  (12–21 UTC)
  B) London     — Pure London only              (07–16 UTC)
  C) Full       — All three combined            (07–21 UTC)

Run: python backtest_session_compare.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import pandas as pd

import backtests.backtest_forex_engine as _engine_mod
from backtests.backtest_forex_engine import (
    run_backtest,
    load_local_env,
    parse_date_utc,
)

load_local_env()

# ── Period ─────────────────────────────────────────────────────────────────────
START_UTC = parse_date_utc("2025-12-18")
END_UTC   = parse_date_utc("2026-03-18")
BALANCE   = 5_000.0
RISK      = 0.05

# ── Session windows (UTC hours, half-open [start, end)) ───────────────────────
SESSIONS = {
    "Current  (12–21 UTC)  Overlap+NY":  (12, 21),
    "London   (07–16 UTC)  Pure London": ( 7, 16),
    "Full     (07–21 UTC)  Lon+Ovlp+NY": ( 7, 21),
}


def run_scenario(label: str, hour_start: int, hour_end: int) -> dict[str, Any]:
    """Patch session_allowed, run backtest, return summary metrics."""

    def _patched_session_allowed(close_time_utc: pd.Timestamp) -> bool:
        return hour_start <= close_time_utc.hour < hour_end

    _engine_mod.session_allowed = _patched_session_allowed

    trades_df, metrics = run_backtest(
        start_utc=START_UTC,
        end_utc=END_UTC,
        starting_balance=BALANCE,
        risk_per_trade=RISK,
    )

    wins   = int(metrics.get("wins",   0))
    losses = int(metrics.get("losses", 0))
    total  = wins + losses
    wr     = float(metrics.get("win_rate", 0.0))
    pf     = float(metrics.get("profit_factor", 0.0))
    bal    = float(metrics.get("ending_balance", BALANCE))
    dd     = float(metrics.get("max_drawdown_r", 0.0))

    return {
        "label":   label,
        "trades":  total,
        "wins":    wins,
        "losses":  losses,
        "wr_pct":  round(wr, 1),
        "pf":      round(pf, 3),
        "balance": round(bal, 2),
        "pnl":     round(bal - BALANCE, 2),
        "max_dd":  round(dd, 1),
    }


def fmt_row(r: dict[str, Any]) -> str:
    pnl_sign = "+" if r["pnl"] >= 0 else ""
    return (
        f"  {r['label']:<42} | "
        f"Trades: {r['trades']:>3} | "
        f"W/L: {r['wins']}/{r['losses']} | "
        f"WR: {r['wr_pct']:>5.1f}% | "
        f"PF: {r['pf']:>5.3f} | "
        f"Balance: ${r['balance']:>9,.2f} ({pnl_sign}${r['pnl']:,.2f}) | "
        f"MaxDD: {r['max_dd']:.1f}%"
    )


def main() -> None:
    print()
    print("=" * 95)
    print("  SESSION FILTER COMPARISON BACKTEST")
    print(f"  Period : {START_UTC.date()} -> {END_UTC.date()}")
    print(f"  Balance: ${BALANCE:,.0f}   Risk: {int(RISK*100)}% per trade")
    print("=" * 95)

    results = []
    for label, (h_start, h_end) in SESSIONS.items():
        print(f"  Running {label} ...", flush=True)
        r = run_scenario(label, h_start, h_end)
        results.append(r)

    print()
    print("-" * 95)
    print("  RESULTS")
    print("-" * 95)
    for r in results:
        print(fmt_row(r))
    print("-" * 95)

    # Verdict
    print()
    london = next(r for r in results if "London" in r["label"])
    current = next(r for r in results if "Current" in r["label"])
    full = next(r for r in results if "Full" in r["label"])

    print("  VERDICT")
    if london["wr_pct"] > current["wr_pct"] and london["pf"] > current["pf"]:
        print("  => London-only session has BETTER WR and PF than current — worth considering.")
    elif london["wr_pct"] < current["wr_pct"]:
        print(f"  => London-only WR ({london['wr_pct']}%) is LOWER than current ({current['wr_pct']}%) — London alone is weaker.")
    else:
        print("  => London-only and current are similar.")

    if full["wr_pct"] >= current["wr_pct"] and full["trades"] > current["trades"]:
        print(f"  => Full session (07-21) adds {full['trades']-current['trades']} extra trades with WR {full['wr_pct']}% — consider expanding.")
    print()


if __name__ == "__main__":
    raise SystemExit(main())
