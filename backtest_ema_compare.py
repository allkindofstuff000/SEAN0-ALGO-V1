"""
EMA Parameter Comparison Backtest
===================================
Compares EMA50/200 (current) vs EMA20/50 (candidate) over 3 months.
Zero changes to production code -- patches IndicatorEngine inline.

Run:
    python backtest_ema_compare.py
"""
from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import backtests.backtest_forex_engine as engine
from core.indicator_engine import IndicatorEngine

# ── Config ────────────────────────────────────────────────────────────────────
START_UTC    = pd.Timestamp("2025-12-18", tz="UTC")   # 3 months back
END_UTC      = pd.Timestamp("2026-03-17 23:59:59", tz="UTC")
BALANCE      = 5_000.0
RISK         = 0.05   # 5 %

SCENARIOS = [
    {"label": "EMA 50/200  (current)",   "ema_fast": 50,  "ema_slow": 200},
    {"label": "EMA 20/50   (candidate)", "ema_fast": 20,  "ema_slow": 50},
]

OUT_PATH = ROOT / "logs" / "ema_compare_results.json"

# ── Patch helper ──────────────────────────────────────────────────────────────
def _patch_indicator_engine(ema_fast: int, ema_slow: int):
    """
    Monkey-patch the IndicatorEngine used inside the backtest engine so
    the chosen EMA periods are used while column names stay ema50/ema200
    (engine reads those names directly).
    """
    original_add = IndicatorEngine.add_indicators

    def patched_add(self, candles: pd.DataFrame) -> pd.DataFrame:
        # Force the periods we want for this run
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        return original_add(self, candles)

    IndicatorEngine.add_indicators = patched_add
    return original_add      # caller must restore this


def _restore_indicator_engine(original_add):
    IndicatorEngine.add_indicators = original_add


# ── Run one scenario ──────────────────────────────────────────────────────────
def run_scenario(label: str, ema_fast: int, ema_slow: int) -> dict:
    print(f"\n  Running {label} ...")
    orig = _patch_indicator_engine(ema_fast, ema_slow)
    try:
        trades_df, metrics = engine.run_backtest(
            start_utc=START_UTC,
            end_utc=END_UTC,
            starting_balance=BALANCE,
            risk_per_trade=RISK,
        )
    finally:
        _restore_indicator_engine(orig)

    total    = int(metrics.get("total_trades", 0))
    wins     = int(metrics.get("winning_trades", 0))
    wr       = float(metrics.get("win_rate", 0.0))
    pf       = float(metrics.get("profit_factor", 0.0))
    end_bal  = float(metrics.get("ending_balance", BALANCE))
    net_pnl  = round(end_bal - BALANCE, 2)

    return {
        "label":            label,
        "ema_fast":         ema_fast,
        "ema_slow":         ema_slow,
        "total_trades":     total,
        "winning_trades":   wins,
        "win_rate_pct":     round(wr, 2),
        "profit_factor":    round(pf, 3),
        "ending_balance":   round(end_bal, 2),
        "net_pnl":          net_pnl,
    }


# ── Print results ─────────────────────────────────────────────────────────────
W = 60

def _bar(char="="):
    return char * W

def _row(label, v1, v2, diff="", better=""):
    print(f"  {label:<28} {str(v1):>10}  {str(v2):>10}  {str(diff):>8}  {better}")

def print_results(r1: dict, r2: dict):
    print(f"\n  {_bar()}")
    print(f"  EMA PARAMETER COMPARISON  --  3 Months  --  ${BALANCE:,.0f} / {RISK*100:.0f}% risk")
    print(f"  {_bar()}")
    print(f"  {'Metric':<28} {'50/200':>10}  {'20/50':>10}  {'Delta':>8}  Note")
    print(f"  {_bar('-')}")

    t1, t2 = r1["total_trades"], r2["total_trades"]
    _row("Total Trades", t1, t2,
         f"+{t2-t1}" if t2 > t1 else str(t2-t1),
         "[MORE]" if t2 > t1 else "[LESS]")

    w1, w2 = r1["win_rate_pct"], r2["win_rate_pct"]
    _row("Win Rate %", f"{w1:.1f}%", f"{w2:.1f}%",
         f"{w2-w1:+.1f}%",
         "[BETTER]" if w2 > w1 else ("[WORSE]" if w2 < w1 else "[SAME]"))

    p1, p2 = r1["profit_factor"], r2["profit_factor"]
    _row("Profit Factor", f"{p1:.3f}", f"{p2:.3f}",
         f"{p2-p1:+.3f}",
         "[BETTER]" if p2 > p1 else ("[WORSE]" if p2 < p1 else "[SAME]"))

    e1, e2 = r1["ending_balance"], r2["ending_balance"]
    _row("Ending Balance $", f"${e1:,.2f}", f"${e2:,.2f}",
         f"${e2-e1:+,.2f}",
         "[BETTER]" if e2 > e1 else ("[WORSE]" if e2 < e1 else "[SAME]"))

    n1, n2 = r1["net_pnl"], r2["net_pnl"]
    _row("Net P&L $", f"${n1:+,.2f}", f"${n2:+,.2f}",
         f"${n2-n1:+,.2f}",
         "[BETTER]" if n2 > n1 else "[WORSE]")

    print(f"  {_bar('-')}")

    # Verdict
    score_2050 = sum([
        t2 > t1,          # more trades
        w2 >= w1,         # same or better win rate
        p2 >= p1,         # same or better PF
        e2 >= e1,         # same or better balance
    ])
    print()
    if score_2050 >= 3:
        print("  VERDICT: [OK] EMA 20/50 wins on", score_2050, "/ 4 metrics")
        print("           Recommend switching to EMA 20/50 for more frequent trades")
    elif score_2050 == 2:
        print("  VERDICT: [~] EMA 20/50 is comparable -- extra trades at similar quality")
    else:
        print("  VERDICT: [NO] EMA 50/200 remains stronger -- stay with current settings")
    print(f"  {_bar()}\n")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"\n{_bar('=')}")
    print("  EMA COMPARISON BACKTEST")
    print(f"  Period : {START_UTC.date()} -> {END_UTC.date()}")
    print(f"  Balance: ${BALANCE:,.0f}   Risk: {RISK*100:.0f}% per trade")
    print(f"{_bar('=')}")

    results = []
    for sc in SCENARIOS:
        r = run_scenario(**sc)
        results.append(r)
        print(f"  [{r['label']}]  trades={r['total_trades']}  "
              f"WR={r['win_rate_pct']:.1f}%  PF={r['profit_factor']:.3f}  "
              f"bal=${r['ending_balance']:,.2f}")

    print_results(results[0], results[1])

    OUT_PATH.parent.mkdir(exist_ok=True)
    OUT_PATH.write_text(json.dumps(results, indent=2))
    print(f"  Results saved -> {OUT_PATH}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
