"""
backtest_htf.py — HTF (1H) Structure Filter Comparison Backtest
================================================================
Wraps the EXISTING engine — zero changes to backtest_forex_engine.py.

Pipeline with HTF filter enabled:
    HTF Filter (1H EMA50 > EMA200) ← NEW, runs FIRST
    -> Trend (15m)
    -> Entry logic (5m)
    -> Other filters
    -> Signal

Usage:
    python backtest_htf.py
    python backtest_htf.py --days 90 --balance 5000 --risk 5
"""
from __future__ import annotations

import argparse
import io
import json
import os
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests

# -- Import existing engine (read-only; zero modifications) -------------------
from backtests import backtest_forex_engine as engine
from core.indicator_engine import IndicatorEngine

ROOT = Path(__file__).resolve().parent.parent

# -- HTF config ----------------------------------------------------------------
HTF_GRANULARITY   = "H1"          # 1-hour candles for higher-timeframe structure
HTF_EMA_FAST      = 50            # EMA fast period on H1
HTF_EMA_SLOW      = 200           # EMA slow period on H1
ENABLE_HTF_FILTER = True          # Global toggle — disable without removing code


# ==============================================================================
#  H1 DATA FETCHING
# ==============================================================================

def fetch_h1_candles(start_utc: pd.Timestamp, end_utc: pd.Timestamp) -> pd.DataFrame:
    """
    Fetch H1 candles from OANDA for the HTF filter.
    Uses the same auth / base-URL as the existing engine.
    On any failure the window is skipped — no crash.
    """
    engine.load_local_env()
    api_key   = os.getenv("OANDA_API_KEY", "").strip()
    base_url  = engine.resolve_oanda_base_url()
    price_cmp = os.getenv("OANDA_PRICE_COMPONENT", "M").strip().upper() or "M"

    frames: list[pd.DataFrame] = []
    windows = engine.build_request_windows(start_utc, end_utc)

    for w_start, w_end in windows:
        url    = f"{base_url}/v3/instruments/{engine.OANDA_INSTRUMENT}/candles"
        params = {
            "granularity": HTF_GRANULARITY,
            "price":       price_cmp,
            "from":        w_start.isoformat(),
            "to":          w_end.isoformat(),
        }
        headers = {
            "Authorization":         f"Bearer {api_key}",
            "Accept-Datetime-Format": "RFC3339",
            "User-Agent":             "SEAN0-ALGO-HTF-backtest/1.0",
        }
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=30)
            # 400 -> future / weekend window — skip silently
            if resp.status_code == 400:
                continue
            resp.raise_for_status()
            frame = engine.candles_to_frame(resp.json().get("candles", []))
            if not frame.empty:
                frames.append(frame)
        except Exception as exc:
            # Fail-safe: log warning, skip window, continue
            print(f"  [HTF] [!] H1 window {w_start.date()}->{w_end.date()} skipped: {exc}")
            continue

    if not frames:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    combined = (
        pd.concat(frames, ignore_index=True)
        .sort_values("timestamp")
        .drop_duplicates(subset=["timestamp"])
        .reset_index(drop=True)
    )
    return combined


# ==============================================================================
#  HTF BIAS LOOKUP
# ==============================================================================

def build_htf_lookup(h1_df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute EMA50 / EMA200 on H1 candles via the existing IndicatorEngine.
    Returns DataFrame indexed by candle open-timestamp.
    """
    if h1_df.empty:
        return pd.DataFrame()

    ind_engine  = IndicatorEngine()
    h1_with_ind = ind_engine.add_indicators(h1_df.copy())
    return h1_with_ind.set_index("timestamp").sort_index()


def get_htf_bias(
    htf_lookup: pd.DataFrame,
    signal_timestamp: pd.Timestamp,
) -> tuple[str, float, float]:
    """
    Return the dominant H1 bias at the time of the 5m signal candle.

    Logic (no look-ahead bias):
      • 5m signal open-time  = signal_timestamp
      • 5m signal close-time = signal_timestamp + 5m
      • The last CLOSED H1 candle at that moment:
            H1 open-time <= signal_timestamp - 1h
        (any H1 candle opened within the last hour is still OPEN)

    Returns:
      ('bullish'|'bearish'|'neutral'|'unknown', ema50, ema200)
    """
    if htf_lookup.empty:
        return "unknown", 0.0, 0.0

    # Only use H1 candles that are fully CLOSED before the 5m signal
    cutoff    = signal_timestamp - pd.Timedelta(hours=1)
    available = htf_lookup[htf_lookup.index <= cutoff]

    if available.empty:
        return "unknown", 0.0, 0.0

    last = available.iloc[-1]

    ema50_raw  = last.get("ema50",  float("nan"))
    ema200_raw = last.get("ema200", float("nan"))

    if pd.isna(ema50_raw) or pd.isna(ema200_raw):
        return "unknown", 0.0, 0.0

    ema50  = float(ema50_raw)
    ema200 = float(ema200_raw)

    if ema50 > ema200:
        bias = "bullish"
    elif ema50 < ema200:
        bias = "bearish"
    else:
        bias = "neutral"

    return bias, ema50, ema200


# ==============================================================================
#  CORE BACKTEST (wraps existing engine, HTF layer on top)
# ==============================================================================

def run_backtest_with_htf(
    *,
    start_utc:        pd.Timestamp,
    end_utc:          pd.Timestamp,
    starting_balance: float = 5_000.0,
    risk_per_trade:   float = 0.05,
    enable_htf:       bool  = True,
    label:            str   = "",
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, int]]:
    """
    Run the full backtest with an optional HTF filter injected BEFORE all
    existing signal logic.  The backtest_forex_engine.py code is untouched.

    Returns:
        (trades_df, metrics_dict, htf_stats_dict)
    """
    warmup_start = start_utc - pd.Timedelta(days=engine.DEFAULT_WARMUP_DAYS)

    tag = f"[{label}]" if label else "[RUN]"
    print(f"\n{tag} Fetching 5m candles "
          f"{warmup_start.date()} -> {end_utc.date()} ...")

    # -- 5m + 15m data (same as existing engine) ------------------------------
    candles_5m  = engine.fetch_historical_5m_candles(warmup_start, end_utc)
    candles_15m = engine.resample_to_15m(candles_5m)

    ind         = IndicatorEngine()
    entry_df    = ind.add_indicators(candles_5m)
    trend_df    = ind.add_indicators(candles_15m)
    trend_lookup = trend_df.set_index("timestamp", drop=False).sort_index()

    # -- H1 data for HTF filter ------------------------------------------------
    htf_lookup    = pd.DataFrame()
    htf_available = False

    if enable_htf:
        print(f"{tag} Fetching H1 candles for HTF filter ...")
        h1_candles = fetch_h1_candles(warmup_start, end_utc)

        if h1_candles.empty:
            print(f"{tag} [!] No H1 candles — HTF filter disabled for this run")
        else:
            htf_lookup    = build_htf_lookup(h1_candles)
            htf_available = not htf_lookup.empty
            print(f"{tag} H1 loaded: {len(h1_candles)} candles  "
                  f"| {len(htf_lookup)} with indicators")

    # -- Backtest loop ---------------------------------------------------------
    trades:       list[dict[str, Any]] = []
    balance       = starting_balance
    htf_stats     = {"allowed": 0, "rejected": 0, "unknown_skipped": 0}

    # Use a throwaway StringIO so the engine's trace writes go nowhere
    null_handle = io.StringIO()

    entry_index = 1
    while entry_index < len(entry_df) - 1:
        signal_timestamp = pd.Timestamp(entry_df.iloc[entry_index]["timestamp"])
        if signal_timestamp >= end_utc:
            break

        # == HTF FILTER — runs FIRST, before all existing logic ===============
        htf_bias  = "unknown"
        htf_ema50 = htf_ema200 = 0.0

        if enable_htf and htf_available:
            htf_bias, htf_ema50, htf_ema200 = get_htf_bias(
                htf_lookup, signal_timestamp
            )
            # Fail-safe: no H1 data yet -> skip trade
            if htf_bias == "unknown":
                htf_stats["unknown_skipped"] += 1
                entry_index += 1
                continue
        # =====================================================================

        # -- Existing engine signal evaluation (UNMODIFIED) -------------------
        evaluation = engine.evaluate_signal(
            entry_df     = entry_df,
            trend_lookup = trend_lookup,
            signal_index = entry_index,
            start_utc    = start_utc,
            end_utc      = end_utc,
            trace_handle = null_handle,
        )

        signal = evaluation.get("signal")
        if signal is None:
            entry_index += 1
            continue

        direction = str(signal["direction"])

        # == HTF DIRECTION CHECK — reject misaligned signals ==================
        if enable_htf and htf_available:
            htf_aligned = (
                (direction == "BUY"  and htf_bias == "bullish") or
                (direction == "SELL" and htf_bias == "bearish")
            )
            if not htf_aligned:
                htf_stats["rejected"] += 1
                entry_index += 1
                continue
            htf_stats["allowed"] += 1
        # =====================================================================

        # -- Trade simulation (existing engine, UNMODIFIED) -------------------
        risk_amount = balance * risk_per_trade
        trade, exit_index = engine.simulate_forex_trade(
            entry_df     = entry_df,
            signal_index = entry_index,
            direction    = direction,
            trend_candle = signal["trend_candle"],
            signal_reason= str(signal["reason"]),
            risk_distance= float(signal["risk_distance"]),
            balance_before = float(balance),
            risk_amount  = float(risk_amount),
            max_hold_bars= engine.DEFAULT_MAX_HOLD,
        )

        if trade is None:
            entry_index += 1
            continue

        balance += float(trade["pnl"])
        trade["equity_after"] = float(balance)
        trades.append(trade)
        entry_index = max(exit_index + 1, entry_index + 1)

    # -- Build results ---------------------------------------------------------
    if trades:
        cols = [
            "timestamp", "entry_timestamp", "exit_timestamp",
            "direction", "entry_price", "exit_price", "sl", "tp",
            "result", "R_multiple", "position_size", "gross_pnl",
            "commission", "pnl", "equity_before", "equity_after",
            "ema50", "ema200", "rsi", "atr", "reason", "exit_reason",
            "bars_held",
        ]
        trades_df = pd.DataFrame(
            trades, columns=[c for c in cols if c in trades[0]]
        )
    else:
        trades_df = pd.DataFrame()

    metrics = engine.compute_metrics(trades_df)
    return trades_df, metrics, htf_stats


# ==============================================================================
#  REPORTING
# ==============================================================================

def _bar(value: float, max_value: float, width: int = 20, fill: str = "#") -> str:
    filled = int(round((value / max_value) * width)) if max_value else 0
    return fill * filled + "." * (width - filled)


def print_comparison(
    metrics_base: dict,
    metrics_htf:  dict,
    htf_stats:    dict,
    start_utc:    pd.Timestamp,
    end_utc:      pd.Timestamp,
    balance:      float,
    risk_pct:     float,
) -> None:
    """Print a formatted side-by-side comparison to the terminal."""
    W = 68

    def hdr(title: str) -> None:
        pad = (W - len(title) - 2) // 2
        print("=" * pad + f" {title} " + "=" * (W - pad - len(title) - 2))

    def row(label: str, base: str, htf: str, highlight: bool = False) -> None:
        mark = " <" if highlight else "  "
        print(f"  {label:<24} {base:>14} {htf:>14}{mark}")

    print("\n" + "=" * W)
    hdr("XAUUSD HTF Structure Filter — Backtest Results")
    print(f"  Period  : {start_utc.date()} -> {end_utc.date()}")
    print(f"  Balance : ${balance:,.0f}   Risk: {risk_pct:.0f}% per trade")
    print(f"  HTF     : 1H EMA{HTF_EMA_FAST} vs EMA{HTF_EMA_SLOW}  "
          f"(BUY if H1-bullish, SELL if H1-bearish)")
    print("-" * W)
    print(f"  {'Metric':<24} {'Baseline':>14} {'+ HTF Filter':>14}")
    print("-" * W)

    # helper
    def pct(v: float) -> str: return f"{v:.1f}%"
    def num(v: float) -> str: return f"{int(v)}"
    def dol(v: float) -> str: return f"${v:,.2f}"
    def r(v: float)   -> str: return f"{v:+.3f}R"
    def pf(v: float)  -> str: return f"{v:.3f}x"

    wr_b  = metrics_base.get("win_rate",        0.0)
    wr_h  = metrics_htf.get( "win_rate",        0.0)
    pf_b  = metrics_base.get("profit_factor",   0.0)
    pf_h  = metrics_htf.get( "profit_factor",   0.0)
    eb_b  = metrics_base.get("ending_balance",  balance)
    eb_h  = metrics_htf.get( "ending_balance",  balance)

    row("Total Trades",      num(metrics_base["total_trades"]), num(metrics_htf["total_trades"]))
    row("Wins",              num(metrics_base["wins"]),         num(metrics_htf["wins"]))
    row("Losses",            num(metrics_base["losses"]),       num(metrics_htf["losses"]))
    row("Win Rate",          pct(wr_b),  pct(wr_h),  highlight=(wr_h > wr_b))
    row("Profit Factor",     pf(pf_b),   pf(pf_h),   highlight=(pf_h > pf_b))
    row("Avg R/Trade",       r(metrics_base["average_r"]),   r(metrics_htf["average_r"]),
        highlight=(metrics_htf["average_r"] > metrics_base["average_r"]))
    row("Max Drawdown R",    r(metrics_base["max_drawdown_r"]), r(metrics_htf["max_drawdown_r"]),
        highlight=(metrics_htf["max_drawdown_r"] > metrics_base["max_drawdown_r"]))
    row("Ending Balance",    dol(eb_b),  dol(eb_h),  highlight=(eb_h > eb_b))
    print("-" * W)

    # Win-rate bars
    max_wr = max(wr_b, wr_h, 1.0)
    print(f"\n  Win Rate Visual")
    print(f"  Baseline   {_bar(wr_b, 100, 30)} {pct(wr_b)}")
    print(f"  HTF Filter {_bar(wr_h, 100, 30)} {pct(wr_h)}")

    # HTF filter stats
    if htf_stats["allowed"] + htf_stats["rejected"] > 0:
        total = htf_stats["allowed"] + htf_stats["rejected"]
        print(f"\n  HTF Filter Statistics")
        print(f"  Signals evaluated  : {total}")
        print(f"  Allowed (aligned)  : {htf_stats['allowed']:4d}  "
              f"({htf_stats['allowed']/total*100:.1f}%)")
        print(f"  Rejected (conflict): {htf_stats['rejected']:4d}  "
              f"({htf_stats['rejected']/total*100:.1f}%)")
        if htf_stats["unknown_skipped"]:
            print(f"  Skipped (no H1 data): {htf_stats['unknown_skipped']}")

    # Verdict
    print("\n" + "=" * W)
    wr_delta  = wr_h  - wr_b
    pf_delta  = pf_h  - pf_b
    eb_delta  = eb_h  - eb_b

    improved  = sum([wr_delta > 0, pf_delta > 0, eb_delta > 0])

    if wr_delta > 0 and pf_delta > 0:
        verdict = "[OK]  HTF FILTER IMPROVED BOTH WIN RATE & PROFIT FACTOR"
        rec     = "->  RECOMMENDED: apply HTF filter to live strategy"
    elif wr_delta > 0:
        verdict = "[OK]  HTF FILTER IMPROVED WIN RATE"
        rec     = f"->  Win rate: {wr_b:.1f}% -> {wr_h:.1f}% (+{wr_delta:.1f}%)"
    elif wr_delta < 0 and pf_delta < 0:
        verdict = "[NO]  HTF FILTER DID NOT IMPROVE — both metrics lower"
        rec     = "->  Consider different HTF parameters or skip this filter"
    elif wr_delta < 0:
        verdict = "[!]   HTF FILTER: Win rate decreased (review alignment)"
        rec     = f"->  Win rate: {wr_b:.1f}% -> {wr_h:.1f}% ({wr_delta:.1f}%)"
    else:
        verdict = "->   HTF FILTER: No change in win rate"
        rec     = "->  Profit factor and balance may still show improvement"

    print(f"\n  {verdict}")
    print(f"  {rec}")
    if eb_delta >= 0:
        print(f"  Ending balance change: {'+' if eb_delta >= 0 else ''}{eb_delta:,.2f}")
    print("\n" + "=" * W + "\n")


# ==============================================================================
#  MAIN
# ==============================================================================

def main(days: int = 90, balance: float = 5_000.0, risk_pct: float = 5.0) -> dict:
    engine.load_local_env()

    risk_fraction = risk_pct / 100.0
    now_utc       = pd.Timestamp.now(tz="UTC")
    # Cap to yesterday to avoid OANDA rejecting partial-day 'to' timestamps
    end_utc   = now_utc.normalize() - pd.Timedelta(seconds=1)
    start_utc = end_utc.normalize() - pd.Timedelta(days=days)

    print(f"\n{'='*68}")
    print(f"  XAUUSD HTF Structure Filter Backtest")
    print(f"  Period  : {start_utc.date()} -> {end_utc.date()} ({days} days)")
    print(f"  Balance : ${balance:,.0f}    Risk : {risk_pct:.0f}% per trade")
    print(f"  HTF     : 1H EMA50 / EMA200 alignment filter")
    print(f"{'='*68}")

    # -- 1/2 Baseline (no HTF filter) -----------------------------------------
    print("\n[1/2] BASELINE — original strategy, no HTF filter")
    df_base, metrics_base, _ = run_backtest_with_htf(
        start_utc        = start_utc,
        end_utc          = end_utc,
        starting_balance = balance,
        risk_per_trade   = risk_fraction,
        enable_htf       = False,
        label            = "BASELINE",
    )
    print(f"  -> {int(metrics_base['total_trades'])} trades  "
          f"WR={metrics_base['win_rate']:.1f}%  "
          f"PF={metrics_base['profit_factor']:.3f}x  "
          f"Balance=${metrics_base['ending_balance']:,.2f}")

    # -- 2/2 With HTF filter ---------------------------------------------------
    print("\n[2/2] HTF FILTER — 1H EMA50/EMA200 alignment enforced")
    df_htf, metrics_htf, htf_stats = run_backtest_with_htf(
        start_utc        = start_utc,
        end_utc          = end_utc,
        starting_balance = balance,
        risk_per_trade   = risk_fraction,
        enable_htf       = True,
        label            = "HTF",
    )
    print(f"  -> {int(metrics_htf['total_trades'])} trades  "
          f"WR={metrics_htf['win_rate']:.1f}%  "
          f"PF={metrics_htf['profit_factor']:.3f}x  "
          f"Balance=${metrics_htf['ending_balance']:,.2f}")

    # -- Print comparison table ------------------------------------------------
    print_comparison(
        metrics_base = metrics_base,
        metrics_htf  = metrics_htf,
        htf_stats    = htf_stats,
        start_utc    = start_utc,
        end_utc      = end_utc,
        balance      = balance,
        risk_pct     = risk_pct,
    )

    # -- Save results JSON -----------------------------------------------------
    results = {
        "period":       {"start": str(start_utc.date()), "end": str(end_utc.date()), "days": days},
        "config":       {"balance": balance, "risk_pct": risk_pct},
        "baseline":     {k: round(float(v), 4) for k, v in metrics_base.items()},
        "htf_filter":   {k: round(float(v), 4) for k, v in metrics_htf.items()},
        "htf_stats":    htf_stats,
        "verdict": {
            "win_rate_delta":     round(metrics_htf["win_rate"]       - metrics_base["win_rate"],       2),
            "profit_factor_delta":round(metrics_htf["profit_factor"]  - metrics_base["profit_factor"],  4),
            "balance_delta":      round(metrics_htf["ending_balance"] - metrics_base["ending_balance"], 2),
            "recommended":        bool(metrics_htf["win_rate"] > metrics_base["win_rate"]),
        },
    }

    out_path = ROOT / "logs" / "htf_backtest_results.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2, default=float), encoding="utf-8")
    print(f"  Results saved -> {out_path}\n")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HTF Structure Filter Backtest")
    parser.add_argument("--days",    type=int,   default=90,     help="Backtest window in days (default: 90)")
    parser.add_argument("--balance", type=float, default=5000.0, help="Starting balance in USD (default: 5000)")
    parser.add_argument("--risk",    type=float, default=5.0,    help="Risk per trade in %% (default: 5)")
    args = parser.parse_args()

    main(days=args.days, balance=args.balance, risk_pct=args.risk)
