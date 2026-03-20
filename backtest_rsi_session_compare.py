"""
RSI Threshold + London Session Comparison Backtest
====================================================
Tests 4 scenarios against the 3-month baseline:

  A) Baseline      : RSI 55/45  + sessions 12-21 UTC  (current live)
  B) RSI only      : RSI 50/50  + sessions 12-21 UTC
  C) London only   : RSI 55/45  + sessions  7-21 UTC  (adds pure London)
  D) Both changes  : RSI 50/50  + sessions  7-21 UTC

Zero changes to production code -- patches module constants inline.

Run:
    python backtest_rsi_session_compare.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import backtests.backtest_forex_engine as engine

# ── Config ────────────────────────────────────────────────────────────────────
START_UTC = pd.Timestamp("2025-12-18", tz="UTC")
END_UTC   = pd.Timestamp("2026-03-17 23:59:59", tz="UTC")
BALANCE   = 5_000.0
RISK      = 0.05

OUT_PATH  = ROOT / "logs" / "rsi_session_compare_results.json"

SCENARIOS = [
    {
        "label":          "A  Baseline   RSI 55/45  sessions 12-21",
        "buy_rsi":        55.0,
        "sell_rsi":       45.0,
        "session_start":  12,
    },
    {
        "label":          "B  RSI only   RSI 50/50  sessions 12-21",
        "buy_rsi":        50.0,
        "sell_rsi":       50.0,
        "session_start":  12,
    },
    {
        "label":          "C  London     RSI 55/45  sessions  7-21",
        "buy_rsi":        55.0,
        "sell_rsi":       45.0,
        "session_start":  7,
    },
    {
        "label":          "D  Both       RSI 50/50  sessions  7-21",
        "buy_rsi":        50.0,
        "sell_rsi":       50.0,
        "session_start":  7,
    },
]

# ── Patch / restore helpers ───────────────────────────────────────────────────
def _apply_patch(buy_rsi: float, sell_rsi: float, session_start: int):
    """Overwrite the three module-level knobs we want to vary."""
    engine.BUY_RSI_THRESHOLD  = buy_rsi
    engine.SELL_RSI_THRESHOLD = sell_rsi

    # Replace session_allowed with a closure that uses session_start
    def _new_session_allowed(close_time_utc: pd.Timestamp) -> bool:
        return session_start <= close_time_utc.hour < 21

    engine.session_allowed = _new_session_allowed


def _restore():
    engine.BUY_RSI_THRESHOLD  = 55.0
    engine.SELL_RSI_THRESHOLD = 45.0

    def _original(close_time_utc: pd.Timestamp) -> bool:
        return 12 <= close_time_utc.hour < 21

    engine.session_allowed = _original


# ── Run one scenario ──────────────────────────────────────────────────────────
def run_scenario(label: str, buy_rsi: float, sell_rsi: float, session_start: int) -> dict:
    print(f"\n  Running {label} ...")
    _apply_patch(buy_rsi, sell_rsi, session_start)
    try:
        trades_df, metrics = engine.run_backtest(
            start_utc=START_UTC,
            end_utc=END_UTC,
            starting_balance=BALANCE,
            risk_per_trade=RISK,
        )
    finally:
        _restore()

    total   = int(metrics.get("total_trades", 0))
    wins    = int(metrics.get("winning_trades", 0))
    wr      = float(metrics.get("win_rate", 0.0))
    pf      = float(metrics.get("profit_factor", 0.0))
    end_bal = float(metrics.get("ending_balance", BALANCE))
    net_pnl = round(end_bal - BALANCE, 2)

    return {
        "label":          label,
        "buy_rsi":        buy_rsi,
        "sell_rsi":       sell_rsi,
        "session_start":  session_start,
        "total_trades":   total,
        "winning_trades": wins,
        "win_rate_pct":   round(wr, 2),
        "profit_factor":  round(pf, 3),
        "ending_balance": round(end_bal, 2),
        "net_pnl":        net_pnl,
    }


# ── Print table ───────────────────────────────────────────────────────────────
W = 72

def _sep(c="-"): return "  " + c * W

def _col(v, w=10): return str(v).rjust(w)

def _delta(v, base, fmt="+.1f", prefix=""):
    diff = v - base
    sign = "+" if diff >= 0 else ""
    return f"{sign}{diff:{fmt}}"

def print_table(results: list[dict]):
    base = results[0]

    print(f"\n{_sep('=')}")
    print(f"  RSI + SESSION COMPARISON  --  3 Months  --  ${BALANCE:,.0f} / {RISK*100:.0f}% risk")
    print(f"  Period: {START_UTC.date()} -> {END_UTC.date()}")
    print(_sep("="))

    hdr = (f"  {'Scenario':<42}"
           f"{'Trades':>7}"
           f"{'WR%':>7}"
           f"{'PF':>7}"
           f"{'Balance':>10}"
           f"{'vs Base':>10}")
    print(hdr)
    print(_sep("-"))

    for i, r in enumerate(results):
        vs_base = "" if i == 0 else f"${r['ending_balance'] - base['ending_balance']:+,.0f}"
        trades_note = "" if i == 0 else f" ({r['total_trades']-base['total_trades']:+d})"
        row = (f"  {r['label']:<42}"
               f"{str(r['total_trades']) + trades_note:>7}"
               f"{r['win_rate_pct']:>6.1f}%"
               f"{r['profit_factor']:>7.3f}"
               f"  ${r['ending_balance']:>8,.2f}"
               f"  {vs_base:>8}")
        print(row)

    print(_sep("-"))

    # Find best overall
    best = max(results[1:], key=lambda r: (r["ending_balance"], r["win_rate_pct"]))

    print()
    print("  BREAKDOWN:")
    print(f"    Baseline trades   : {base['total_trades']}")
    for r in results[1:]:
        extra = r['total_trades'] - base['total_trades']
        wr_diff = r['win_rate_pct'] - base['win_rate_pct']
        pf_diff = r['profit_factor'] - base['profit_factor']
        bal_diff = r['ending_balance'] - base['ending_balance']
        sign_t = "+" if extra >= 0 else ""
        sign_w = "+" if wr_diff >= 0 else ""
        sign_p = "+" if pf_diff >= 0 else ""
        sign_b = "+" if bal_diff >= 0 else ""
        quality = "PROFITABLE" if r['ending_balance'] > BALANCE else "LOSING"
        print(f"    {r['label'].split()[0]}  "
              f"trades={r['total_trades']}({sign_t}{extra})  "
              f"WR={r['win_rate_pct']:.1f}%({sign_w}{wr_diff:.1f}%)  "
              f"PF={r['profit_factor']:.3f}({sign_p}{pf_diff:.3f})  "
              f"bal=${r['ending_balance']:,.0f}({sign_b}${bal_diff:,.0f})  "
              f"[{quality}]")

    print()
    if best["ending_balance"] > base["ending_balance"] and best["win_rate_pct"] >= base["win_rate_pct"] - 3:
        print(f"  VERDICT: [OK] Best upgrade -> Scenario {best['label'].split()[0]}")
        print(f"           Trades: {base['total_trades']} -> {best['total_trades']} "
              f"({best['total_trades']-base['total_trades']:+d})")
        print(f"           WR: {base['win_rate_pct']:.1f}% -> {best['win_rate_pct']:.1f}%")
        print(f"           PF: {base['profit_factor']:.3f} -> {best['profit_factor']:.3f}")
        print(f"           Balance: ${base['ending_balance']:,.2f} -> ${best['ending_balance']:,.2f}")
    else:
        print(f"  VERDICT: [HOLD] No improvement found. Baseline stays best.")
        print(f"           Current: {base['total_trades']} trades, "
              f"{base['win_rate_pct']:.1f}% WR, ${base['ending_balance']:,.2f}")

    print(_sep("="))
    print()


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"\n{'='*72}")
    print("  RSI THRESHOLD + LONDON SESSION BACKTEST COMPARISON")
    print(f"  Scenarios: A=baseline  B=RSI50/50  C=+London  D=RSI50+London")
    print(f"{'='*72}")

    results = []
    for sc in SCENARIOS:
        r = run_scenario(**sc)
        results.append(r)
        print(f"  [{r['label'][:2]}] trades={r['total_trades']:>3}  "
              f"WR={r['win_rate_pct']:.1f}%  PF={r['profit_factor']:.3f}  "
              f"bal=${r['ending_balance']:,.2f}")

    print_table(results)

    OUT_PATH.parent.mkdir(exist_ok=True)
    OUT_PATH.write_text(json.dumps(results, indent=2))
    print(f"  Saved -> {OUT_PATH}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
