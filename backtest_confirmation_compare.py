"""
Entry Confirmation Comparison Backtest
=======================================
Tests 3 confirmation methods alongside the Baseline to find the best edge booster.

Scenarios
---------
  Baseline  : Current strategy (no extra confirmation)
  Option A  : Strong Close   — body must be > 60% of candle range (no indecision candles)
  Option B  : Consecutive Direction — entry candle AND prev candle both same direction
  Option C  : ATR Breakout Margin  — close must clear prev high/low by 0.3×ATR (quality breakouts only)

Run
---
  python backtest_confirmation_compare.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, TextIO

import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# ── load .env ──────────────────────────────────────────────────────────────
_ENV = ROOT / ".env"
if _ENV.exists():
    for _line in _ENV.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        _k = _k.strip()
        _v = _v.strip().strip('"').strip("'")
        if _k and _k not in os.environ:
            os.environ[_k] = _v

import backtests.backtest_forex_engine as engine
from backtests.backtest_forex_engine import (
    evaluate_signal as _original_evaluate_signal,
    BUY_RSI_THRESHOLD,
    SELL_RSI_THRESHOLD,
)

# ── config ─────────────────────────────────────────────────────────────────
START   = "2025-12-18"
END     = "2026-03-17"
BALANCE = 5_000.0
RISK    = 0.05

START_UTC = pd.Timestamp(START, tz="UTC")
END_UTC   = pd.Timestamp(END,   tz="UTC")

# ── confirmation helpers ───────────────────────────────────────────────────

def body_ratio(candle: pd.Series) -> float:
    """Returns what fraction of the full range is the candle body (0–1)."""
    hi = float(candle["high"])
    lo = float(candle["low"])
    full_range = hi - lo
    if full_range <= 0:
        return 0.0
    body = abs(float(candle["close"]) - float(candle["open"]))
    return body / full_range


def is_bullish(candle: pd.Series) -> bool:
    return float(candle["close"]) > float(candle["open"])


def is_bearish(candle: pd.Series) -> bool:
    return float(candle["close"]) < float(candle["open"])


# ── patched evaluate_signal per scenario ──────────────────────────────────

def make_patched(mode: str):
    """Return a patched evaluate_signal that wraps the original with extra checks."""

    def patched(
        *,
        entry_df: pd.DataFrame,
        trend_lookup: pd.DataFrame,
        signal_index: int,
        start_utc: pd.Timestamp,
        end_utc: pd.Timestamp,
        trace_handle: TextIO,
    ) -> dict[str, Any]:
        # Run the original strategy first
        result = _original_evaluate_signal(
            entry_df=entry_df,
            trend_lookup=trend_lookup,
            signal_index=signal_index,
            start_utc=start_utc,
            end_utc=end_utc,
            trace_handle=trace_handle,
        )

        # Only apply extra filter when baseline accepted
        if result["reason"] != "accepted" or result["signal"] is None:
            return result

        candle   = entry_df.iloc[signal_index]
        previous = entry_df.iloc[signal_index - 1]
        direction = str(result["signal"]["direction"])
        atr_value = float(candle["atr14"]) if "atr14" in candle else 0.0

        if mode == "A":
            # Strong Close: body > 60% of range
            ratio = body_ratio(candle)
            passed = ratio > 0.60
            trace_handle.write(
                f"[CONFIRM-A] strong_close body_ratio={ratio:.2f} -> {'ok' if passed else 'rejected'}\n\n"
            )
            if not passed:
                return {"signal": None, "reason": "weak_close_body"}

        elif mode == "B":
            # Consecutive Direction: both entry candle and prev candle in signal direction
            if direction == "BUY":
                passed = is_bullish(candle) and is_bullish(previous)
            else:
                passed = is_bearish(candle) and is_bearish(previous)
            trace_handle.write(
                f"[CONFIRM-B] consecutive_direction={direction} -> {'ok' if passed else 'rejected'}\n\n"
            )
            if not passed:
                return {"signal": None, "reason": "no_consecutive_direction"}

        elif mode == "C":
            # ATR Breakout Margin: breakout must clear prev extreme by 0.3×ATR
            margin = atr_value * 0.30
            if direction == "BUY":
                required = float(previous["high"]) + margin
                passed = float(candle["close"]) > required
                trace_handle.write(
                    f"[CONFIRM-C] atr_margin close={float(candle['close']):.2f} > "
                    f"prev_high+0.3ATR={required:.2f} -> {'ok' if passed else 'rejected'}\n\n"
                )
            else:
                required = float(previous["low"]) - margin
                passed = float(candle["close"]) < required
                trace_handle.write(
                    f"[CONFIRM-C] atr_margin close={float(candle['close']):.2f} < "
                    f"prev_low-0.3ATR={required:.2f} -> {'ok' if passed else 'rejected'}\n\n"
                )
            if not passed:
                return {"signal": None, "reason": "insufficient_atr_margin"}

        return result

    return patched


# ── run one scenario ───────────────────────────────────────────────────────

def run_scenario(label: str, mode: str | None) -> dict[str, Any]:
    """Patch the engine, run backtest, restore."""
    if mode is None:
        patched_fn = _original_evaluate_signal
    else:
        patched_fn = make_patched(mode)

    engine.evaluate_signal = patched_fn  # type: ignore[attr-defined]
    try:
        trades_df, metrics = engine.run_backtest(
            start_utc=START_UTC,
            end_utc=END_UTC,
            starting_balance=BALANCE,
            risk_per_trade=RISK,
        )
    finally:
        engine.evaluate_signal = _original_evaluate_signal  # type: ignore[attr-defined]

    trades = int(metrics.get("total_trades", 0))
    wr     = float(metrics.get("win_rate", 0.0))   # already in % (0-100)
    pf     = float(metrics.get("profit_factor", 0.0))
    bal    = float(metrics.get("ending_balance", BALANCE))

    return {
        "label":   label,
        "trades":  trades,
        "wr":      wr,
        "pf":      pf,
        "balance": bal,
    }


# ── main ───────────────────────────────────────────────────────────────────

SCENARIOS = [
    ("Baseline  (no extra filter)", None),
    ("Option A  Strong Close >60%",  "A"),
    ("Option B  Consecutive Candles","B"),
    ("Option C  ATR Breakout Margin","C"),
]


def main() -> int:
    print()
    print("=" * 68)
    print("  ENTRY CONFIRMATION COMPARISON BACKTEST")
    print(f"  Period : {START} -> {END}")
    print(f"  Balance: ${BALANCE:,.0f}   Risk: {RISK*100:.0f}% per trade")
    print("=" * 68)

    results: list[dict[str, Any]] = []
    for label, mode in SCENARIOS:
        print(f"  Running {label} ...", flush=True)
        r = run_scenario(label, mode)
        results.append(r)

    # ── table ───────────────────────────────────────────────────────────
    print()
    print("=" * 68)
    print(f"  {'Scenario':<35} {'Trades':>6} {'WR%':>6} {'PF':>6} {'Balance':>10}")
    print("-" * 68)

    baseline = results[0]
    for r in results:
        flag = ""
        if r["label"] != baseline["label"]:
            # highlight if strictly better on WR and balance
            if r["wr"] > baseline["wr"] and r["balance"] > baseline["balance"]:
                flag = " << BETTER"
            elif r["wr"] < baseline["wr"] or r["balance"] < baseline["balance"]:
                flag = " !! WORSE"
        print(
            f"  {r['label']:<35} {r['trades']:>6} {r['wr']:>5.1f}% {r['pf']:>6.3f} "
            f"${r['balance']:>9,.0f}{flag}"
        )

    print("=" * 68)
    print()

    # ── verdict ─────────────────────────────────────────────────────────
    best = max(results[1:], key=lambda x: (x["wr"], x["balance"]))
    print("VERDICT")
    print("-------")

    for r in results[1:]:
        trade_change = r["trades"] - baseline["trades"]
        wr_change    = r["wr"]      - baseline["wr"]
        bal_change   = r["balance"] - baseline["balance"]
        sign_t = "+" if trade_change >= 0 else ""
        sign_w = "+" if wr_change    >= 0 else ""
        sign_b = "+" if bal_change   >= 0 else ""
        print(
            f"  {r['label'].split('(')[0].strip()}: "
            f"trades {sign_t}{trade_change}, "
            f"WR {sign_w}{wr_change:.1f}%, "
            f"balance {sign_b}${bal_change:,.0f}"
        )

    print()
    if best["wr"] > baseline["wr"] and best["balance"] > baseline["balance"]:
        print(f"  >> RECOMMENDED: {best['label'].strip()}")
        print(f"     WR {baseline['wr']:.1f}% -> {best['wr']:.1f}%  |  "
              f"Balance ${baseline['balance']:,.0f} -> ${best['balance']:,.0f}")
    else:
        print("  >> No option clearly beats the Baseline — keep current strategy.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
