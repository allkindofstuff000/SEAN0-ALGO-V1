"""
Engulfing Pattern Comparison Backtest
=======================================
Tests 4 scenarios over 3 months, zero changes to production code.

  Baseline   : No engulfing  (current strategy)
  Option A   : Hard Filter   — signal ONLY if engulfing present
  Option B   : Score Bonus   — engulfing adds extra weight (lowers score threshold)
  Option C   : OR Condition  — price breakout OR engulfing (either is enough)

Run:
    python backtest_engulfing_compare.py
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import backtests.backtest_forex_engine as engine

# ── Config ────────────────────────────────────────────────────────────────────
START_UTC = pd.Timestamp("2025-12-18", tz="UTC")
END_UTC   = pd.Timestamp("2026-03-17 23:59:59", tz="UTC")
BALANCE   = 5_000.0
RISK      = 0.05   # 5%

OUT_PATH  = ROOT / "logs" / "engulfing_compare_results.json"

W = 68   # line width

# ── Engulfing detection ────────────────────────────────────────────────────────
def is_engulfing(candle: pd.Series, previous: pd.Series, direction: str) -> bool:
    """
    True if the current candle fully engulfs the previous candle body.

    Bullish (BUY):  current open <= prev close  AND  current close >= prev open
                    AND  current body > prev body  AND  candle is green (close > open)
    Bearish (SELL): current open >= prev close  AND  current close <= prev open
                    AND  current body > prev body  AND  candle is red (close < open)
    """
    c_open  = float(candle["open"])
    c_close = float(candle["close"])
    p_open  = float(previous["open"])
    p_close = float(previous["close"])

    c_body = abs(c_close - c_open)
    p_body = abs(p_close - p_open)

    if direction == "BUY":
        return (
            c_close > c_open          # green candle
            and c_open  <= p_close    # opens at or below prev close
            and c_close >= p_open     # closes at or above prev open
            and c_body  >  p_body     # body is larger than prev
        )
    else:  # SELL
        return (
            c_close < c_open          # red candle
            and c_open  >= p_close    # opens at or above prev close
            and c_close <= p_open     # closes at or below prev open
            and c_body  >  p_body     # body is larger than prev
        )


# ── Patch engine per scenario ─────────────────────────────────────────────────
_original_evaluate = engine.evaluate_signal


def _make_patched(mode: str):
    """
    Returns a replacement evaluate_signal function that injects engulfing logic.

    mode:
      'baseline'  — no change (original logic)
      'hard'      — breakout_ok AND engulfing_ok
      'score'     — breakout_ok, but engulfing lowers threshold by 20 pts
      'or'        — breakout_ok OR engulfing_ok
    """
    def patched_evaluate_signal(
        entry_df, trend_lookup, signal_index, start_utc, end_utc, trace_handle
    ):
        if mode == "baseline":
            return _original_evaluate(
                entry_df=entry_df, trend_lookup=trend_lookup,
                signal_index=signal_index, start_utc=start_utc,
                end_utc=end_utc, trace_handle=trace_handle,
            )

        # Run base evaluation
        result = _original_evaluate(
            entry_df=entry_df, trend_lookup=trend_lookup,
            signal_index=signal_index, start_utc=start_utc,
            end_utc=end_utc, trace_handle=trace_handle,
        )

        # Only modify accepted or near-accepted signals
        # We re-evaluate the engulfing on every candle that passed trend check
        # For simplicity: we intercept at the signal level

        candle   = entry_df.iloc[signal_index]
        previous = entry_df.iloc[signal_index - 1]

        # Determine direction from trend EMAs
        try:
            from backtests.backtest_forex_engine import trend_candle_timestamp
            ts = pd.Timestamp(candle["timestamp"])
            tt = trend_candle_timestamp(ts)
            if tt not in trend_lookup.index:
                return result
            tc = trend_lookup.loc[tt]
            if isinstance(tc, pd.DataFrame):
                tc = tc.iloc[-1]
            ema50  = float(tc["ema50"])
            ema200 = float(tc["ema200"])
            direction = "BUY" if ema50 > ema200 else ("SELL" if ema50 < ema200 else None)
        except Exception:
            return result

        if direction is None:
            return result

        engulf = is_engulfing(candle, previous, direction)

        if mode == "hard":
            # Signal only if engulfing present
            if result["reason"] == "accepted" and not engulf:
                return {"signal": None, "reason": "no_engulfing"}
            # If was rejected for no_breakout but engulfing IS present → still reject
            # (hard mode: we only ADD the engulfing requirement, not bypass breakout)
            return result

        elif mode == "score":
            # Engulfing as score bonus: if signal was borderline (no_breakout rejected)
            # but engulfing IS present → allow it (acts as breakout replacement)
            if result["reason"] == "accepted":
                return result   # already passing, bonus doesn't change outcome
            if result["reason"] == "no_breakout" and engulf:
                # Engulfing substitutes for breakout → promote to accepted
                return {
                    "signal": {
                        "direction": direction,
                        "trend_candle": trend_lookup.loc[
                            trend_candle_timestamp(pd.Timestamp(candle["timestamp"]))
                        ],
                        "risk_distance": float(candle["atr14"]) * engine.STOP_LOSS_ATR_MULTIPLIER,
                        "atr_value": float(candle["atr14"]),
                        "reason": "engulfing_bonus",
                    },
                    "reason": "accepted",
                }
            return result

        elif mode == "or":
            # Breakout OR engulfing — if breakout failed but engulfing present → allow
            if result["reason"] == "no_breakout" and engulf:
                return {
                    "signal": {
                        "direction": direction,
                        "trend_candle": trend_lookup.loc[
                            trend_candle_timestamp(pd.Timestamp(candle["timestamp"]))
                        ],
                        "risk_distance": float(candle["atr14"]) * engine.STOP_LOSS_ATR_MULTIPLIER,
                        "atr_value": float(candle["atr14"]),
                        "reason": "engulfing_or_breakout",
                    },
                    "reason": "accepted",
                }
            return result

        return result

    return patched_evaluate_signal


# ── Run one scenario ──────────────────────────────────────────────────────────
def run_scenario(label: str, mode: str) -> dict:
    print(f"  Running {label} ...")

    # Patch
    engine.evaluate_signal = _make_patched(mode)
    try:
        trades_df, metrics = engine.run_backtest(
            start_utc=START_UTC,
            end_utc=END_UTC,
            starting_balance=BALANCE,
            risk_per_trade=RISK,
        )
    finally:
        engine.evaluate_signal = _original_evaluate   # always restore

    total   = int(metrics.get("total_trades", 0))
    wins    = int(metrics.get("winning_trades", 0))
    wr      = float(metrics.get("win_rate", 0.0))
    pf      = float(metrics.get("profit_factor", 0.0))
    end_bal = float(metrics.get("ending_balance", BALANCE))
    net_pnl = round(end_bal - BALANCE, 2)
    avg_r   = float(metrics.get("avg_r_multiple", 0.0) or 0.0)

    return {
        "label":          label,
        "mode":           mode,
        "total_trades":   total,
        "winning_trades": wins,
        "win_rate_pct":   round(wr, 2),
        "profit_factor":  round(pf, 3),
        "ending_balance": round(end_bal, 2),
        "net_pnl":        net_pnl,
        "avg_r":          round(avg_r, 3),
    }


# ── Print results ─────────────────────────────────────────────────────────────
def _row(label, *vals):
    col_w = 12
    print("  " + f"{label:<26}" + "".join(f"{str(v):>{col_w}}" for v in vals))

def _bar(char="="):
    return char * W

def print_results(results: list[dict]):
    base = results[0]

    print(f"\n  {_bar()}")
    print(f"  ENGULFING PATTERN COMPARISON  —  3 Months  —  ${BALANCE:,.0f} / {RISK*100:.0f}% risk")
    print(f"  {_bar()}")

    headers = ["Metric"] + [r["label"].split("(")[0].strip() for r in results]
    _row(*headers)
    print(f"  {_bar('-')}")

    def delta(v, base_v, higher_better=True):
        d = v - base_v
        if d == 0: return "—"
        sign = "+" if d > 0 else ""
        better = (d > 0) == higher_better
        tag = " [OK]" if better else " [!!]"
        return f"{sign}{d:.1f}{tag}" if isinstance(d, float) else f"{sign}{d}{tag}"

    # Trades row
    trades_vals = [r["total_trades"] for r in results]
    _row("Total Trades",
         *[f"{r['total_trades']}" + ("" if i == 0 else
           f"  ({'+' if r['total_trades']-base['total_trades']>=0 else ''}{r['total_trades']-base['total_trades']})")
           for i, r in enumerate(results)])

    # Win rate
    wr_vals = [r["win_rate_pct"] for r in results]
    _row("Win Rate %",
         *[f"{r['win_rate_pct']:.1f}%" + ("" if i == 0 else
           f"  ({'+' if r['win_rate_pct']-base['win_rate_pct']>=0 else ''}{r['win_rate_pct']-base['win_rate_pct']:.1f}%)")
           for i, r in enumerate(results)])

    # PF
    _row("Profit Factor",
         *[f"{r['profit_factor']:.3f}" + ("" if i == 0 else
           f"  ({'+' if r['profit_factor']-base['profit_factor']>=0 else ''}{r['profit_factor']-base['profit_factor']:.3f})")
           for i, r in enumerate(results)])

    # Balance
    _row("Ending Balance",
         *[f"${r['ending_balance']:,.0f}" + ("" if i == 0 else
           f"  ({'+' if r['net_pnl']-base['net_pnl']>=0 else ''}${r['net_pnl']-base['net_pnl']:,.0f})")
           for i, r in enumerate(results)])

    print(f"  {_bar('-')}")

    # Verdict per option
    print()
    for r in results[1:]:
        t_diff = r["total_trades"] - base["total_trades"]
        w_diff = r["win_rate_pct"] - base["win_rate_pct"]
        p_diff = r["profit_factor"] - base["profit_factor"]
        b_diff = r["ending_balance"] - base["ending_balance"]

        wins_on = sum([
            t_diff >= -20,       # not too many trades lost
            w_diff >= 0,         # win rate same or better
            p_diff >= 0,         # PF same or better
            b_diff >= 0,         # balance same or better
        ])

        label = r["label"]
        if wins_on == 4:
            verdict = "[BEST]  Recommend implementing"
        elif wins_on == 3:
            verdict = "[GOOD]  Worth considering"
        elif wins_on == 2:
            verdict = "[MID]   Mixed results"
        else:
            verdict = "[SKIP]  Not recommended"

        print(f"  {label:<38} -> {verdict}")

    print(f"\n  {_bar()}\n")


# ── Main ──────────────────────────────────────────────────────────────────────
SCENARIOS = [
    ("Baseline  (no engulfing)",       "baseline"),
    ("Option A  (hard filter)",        "hard"),
    ("Option B  (score bonus)",        "score"),
    ("Option C  (OR condition)",       "or"),
]

def main():
    print(f"\n{'='*W}")
    print("  ENGULFING PATTERN COMPARISON BACKTEST")
    print(f"  Period : {START_UTC.date()} -> {END_UTC.date()}")
    print(f"  Balance: ${BALANCE:,.0f}   Risk: {RISK*100:.0f}% per trade")
    print(f"{'='*W}")

    results = []
    for label, mode in SCENARIOS:
        r = run_scenario(label, mode)
        results.append(r)
        print(f"  [{r['label']:<38}]  "
              f"trades={r['total_trades']:>3}  "
              f"WR={r['win_rate_pct']:.1f}%  "
              f"PF={r['profit_factor']:.3f}  "
              f"bal=${r['ending_balance']:,.2f}")

    print_results(results)

    OUT_PATH.parent.mkdir(exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"  Results saved -> {OUT_PATH}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
