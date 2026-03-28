"""
RSI EMA ETH Parameter Optimizer — READ-ONLY testing.
Tests parameter combinations on historical data to find optimal settings.
Does NOT modify any live strategy files.
"""
from __future__ import annotations

import itertools
import logging
import time
from typing import Any

log = logging.getLogger(__name__)


# ── Indicator helpers (same as backtester.py) ─────────────

def _ema(values: list[float], period: int) -> list[float | None]:
    if len(values) < period:
        return [None] * len(values)
    mult = 2.0 / (period + 1)
    result: list[float | None] = [None] * (period - 1)
    sma = sum(values[:period]) / period
    result.append(sma)
    for i in range(period, len(values)):
        sma = values[i] * mult + result[-1] * (1 - mult)
        result.append(sma)
    return result


def _rsi(closes: list[float], period: int = 14) -> list[float | None]:
    if len(closes) < period + 1:
        return [None] * len(closes)
    result: list[float | None] = [None] * period
    gains, losses = [], []
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    rs = avg_gain / avg_loss if avg_loss > 0 else 100
    result.append(100 - 100 / (1 + rs))
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(d, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-d, 0)) / period
        rs = avg_gain / avg_loss if avg_loss > 0 else 100
        result.append(100 - 100 / (1 + rs))
    return result


def _atr(candles: list[dict], period: int = 14) -> list[float | None]:
    if len(candles) < 2:
        return [None] * len(candles)
    trs: list[float | None] = [None]
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    result: list[float | None] = [None] * period
    if len(trs) > period:
        avg = sum(t for t in trs[1:period + 1] if t is not None) / period
        result.append(avg)
        for i in range(period + 1, len(trs)):
            avg = (avg * (period - 1) + (trs[i] or 0)) / period
            result.append(avg)
    return result


# ── Single backtest run with custom params ────────────────

def _backtest_combo(
    candles: list[dict],
    rsi_period: int,
    rsi_ob: int,
    rsi_os: int,
    ema_fast: int,
    ema_slow: int,
    entry_option: str,
    atr_mult: float,
    tp_ratio: float,
    starting_balance: float = 10000.0,
    risk_per_trade: float = 0.0075,
) -> dict:
    """Run one backtest with specific parameters."""
    warmup = max(rsi_period, ema_slow) + 2
    if len(candles) < warmup + 10:
        return {"trades": 0, "winRate": 0, "pf": 0, "totalR": 0, "totalPnl": 0}

    closes = [c["close"] for c in candles]
    rsi_vals = _rsi(closes, rsi_period)
    ema_f = _ema(closes, ema_fast)
    ema_s = _ema(closes, ema_slow)
    atr_vals = _atr(candles, 14)

    balance = starting_balance
    trades = []
    open_trade = None

    for i in range(warmup, len(candles) - 1):
        price = candles[i]["close"]
        curr_rsi = rsi_vals[i] if i < len(rsi_vals) else None
        prev_rsi = rsi_vals[i - 1] if i - 1 < len(rsi_vals) else None
        fast_now = ema_f[i] if i < len(ema_f) else None
        fast_prev = ema_f[i - 1] if i - 1 < len(ema_f) else None
        slow_now = ema_s[i] if i < len(ema_s) else None
        slow_prev = ema_s[i - 1] if i - 1 < len(ema_s) else None
        atr_val = atr_vals[i] if i < len(atr_vals) else None

        if any(v is None for v in [curr_rsi, prev_rsi, fast_now, fast_prev, slow_now, slow_prev, atr_val]):
            continue

        # ── Manage open trade ──
        if open_trade is not None:
            c = candles[i]
            if open_trade["dir"] == "LONG":
                if c["low"] <= open_trade["sl"]:
                    pnl = -balance * risk_per_trade
                    balance += pnl
                    trades.append({"result": "LOSS", "pnlR": -1.0, "pnl": pnl, "time": c["time"]})
                    open_trade = None
                    continue
                if c["high"] >= open_trade["tp"]:
                    rr = open_trade["rr"]
                    pnl = balance * risk_per_trade * rr
                    balance += pnl
                    trades.append({"result": "WIN", "pnlR": rr, "pnl": pnl, "time": c["time"]})
                    open_trade = None
                    continue
            else:  # SHORT
                if c["high"] >= open_trade["sl"]:
                    pnl = -balance * risk_per_trade
                    balance += pnl
                    trades.append({"result": "LOSS", "pnlR": -1.0, "pnl": pnl, "time": c["time"]})
                    open_trade = None
                    continue
                if c["low"] <= open_trade["tp"]:
                    rr = open_trade["rr"]
                    pnl = balance * risk_per_trade * rr
                    balance += pnl
                    trades.append({"result": "WIN", "pnlR": rr, "pnl": pnl, "time": c["time"]})
                    open_trade = None
                    continue
            continue

        # ── Check entry signal ──
        signal = _check_entry(
            price, curr_rsi, prev_rsi,
            fast_now, fast_prev, slow_now, slow_prev,
            entry_option, rsi_ob, rsi_os,
        )
        if signal is None:
            continue

        # Build trade
        sl_dist = atr_val * atr_mult
        if signal == "LONG":
            sl = price - sl_dist
            tp = price + sl_dist * tp_ratio
        else:
            sl = price + sl_dist
            tp = price - sl_dist * tp_ratio

        rr = tp_ratio
        open_trade = {"dir": signal, "entry": price, "sl": sl, "tp": tp, "rr": rr}

    # ── Calculate metrics ──
    total = len(trades)
    if total == 0:
        return {"trades": 0, "winRate": 0, "pf": 0, "totalR": 0, "totalPnl": 0, "finalBalance": starting_balance}

    wins = sum(1 for t in trades if t["result"] == "WIN")
    gross_w = sum(t["pnlR"] for t in trades if t["result"] == "WIN")
    gross_l = abs(sum(t["pnlR"] for t in trades if t["result"] == "LOSS"))
    total_pnl = sum(t["pnl"] for t in trades)

    return {
        "trades": total,
        "wins": wins,
        "losses": total - wins,
        "winRate": round(wins / total * 100, 1),
        "pf": round(gross_w / gross_l, 2) if gross_l > 0 else round(gross_w, 2),
        "totalR": round(gross_w - gross_l, 2),
        "totalPnl": round(total_pnl, 2),
        "finalBalance": round(balance, 2),
    }


def _check_entry(
    price: float,
    curr_rsi: float, prev_rsi: float,
    fast_now: float, fast_prev: float,
    slow_now: float, slow_prev: float,
    entry_option: str, rsi_ob: int, rsi_os: int,
) -> str | None:
    """Check entry conditions for a given entry option. Returns 'LONG', 'SHORT', or None."""
    rsi_bull_cross = prev_rsi <= rsi_os and curr_rsi > rsi_os
    rsi_bear_cross = prev_rsi >= rsi_ob and curr_rsi < rsi_ob
    ema_bull_cross = fast_prev <= slow_prev and fast_now > slow_now
    ema_bear_cross = fast_prev >= slow_prev and fast_now < slow_now
    price_above_ema = price > slow_now
    price_below_ema = price < slow_now

    if entry_option == "A":
        if rsi_bull_cross:
            return "LONG"
        if rsi_bear_cross:
            return "SHORT"
    elif entry_option == "B":
        if ema_bull_cross:
            return "LONG"
        if ema_bear_cross:
            return "SHORT"
    elif entry_option == "C":
        if rsi_bull_cross and price_above_ema:
            return "LONG"
        if rsi_bear_cross and price_below_ema:
            return "SHORT"
    elif entry_option == "D":
        if ema_bull_cross and curr_rsi < 60:
            return "LONG"
        if ema_bear_cross and curr_rsi > 40:
            return "SHORT"
    elif entry_option == "E":
        if price_above_ema and prev_rsi < 40 and curr_rsi >= 40:
            return "LONG"
        if price_below_ema and prev_rsi > 60 and curr_rsi <= 60:
            return "SHORT"
    return None


# ── Main optimizer ────────────────────────────────────────

def run_optimizer(
    candles_5m: list[dict],
    candles_15m: list[dict],
    starting_balance: float = 10000.0,
    risk_per_trade: float = 0.0075,
    progress_callback=None,
) -> dict:
    """
    Test all parameter combinations on 5M and 15M candles.
    Returns sorted results with top 10 and baseline comparison.
    """
    rsi_periods = [7, 9, 14, 21]
    rsi_ob_list = [65, 70, 75]
    rsi_os_list = [25, 30, 35]
    ema_fast_list = [9, 20, 50]
    ema_slow_list = [50, 100, 200]
    entry_options = ["A", "B", "C", "D", "E"]
    atr_mults = [1.0, 1.5, 2.0]
    tp_ratios = [1.5, 2.0, 2.5, 3.0]

    # Build valid combinations
    combos = []
    for rp, rob, ros, ef, es, eo, am, tp in itertools.product(
        rsi_periods, rsi_ob_list, rsi_os_list,
        ema_fast_list, ema_slow_list,
        entry_options, atr_mults, tp_ratios,
    ):
        if ef >= es:
            continue  # fast must be < slow
        if ros >= rob:
            continue  # OS must be < OB
        combos.append({
            "rsiPeriod": rp, "rsiOB": rob, "rsiOS": ros,
            "emaFast": ef, "emaSlow": es,
            "entryOption": eo, "atrMult": am, "tpRatio": tp,
        })

    total = len(combos)
    log.info("[OPTIMIZER] Testing %d combinations on %d 5M + %d 15M candles",
             total, len(candles_5m), len(candles_15m))

    results = []
    t0 = time.time()

    for idx, combo in enumerate(combos):
        if progress_callback and idx % 100 == 0:
            progress_callback(idx, total)

        r5 = _backtest_combo(
            candles_5m,
            combo["rsiPeriod"], combo["rsiOB"], combo["rsiOS"],
            combo["emaFast"], combo["emaSlow"],
            combo["entryOption"], combo["atrMult"], combo["tpRatio"],
            starting_balance, risk_per_trade,
        )
        r15 = _backtest_combo(
            candles_15m,
            combo["rsiPeriod"], combo["rsiOB"], combo["rsiOS"],
            combo["emaFast"], combo["emaSlow"],
            combo["entryOption"], combo["atrMult"], combo["tpRatio"],
            starting_balance, risk_per_trade,
        )

        # Pick the better timeframe
        best = r5 if r5["winRate"] > r15["winRate"] else r15
        best_tf = "5M" if r5["winRate"] > r15["winRate"] else "15M"

        results.append({
            "params": combo,
            "tf5m": r5,
            "tf15m": r15,
            "best": {**best, "tf": best_tf},
        })

    elapsed = round(time.time() - t0, 1)

    # Filter results with at least 5 trades
    valid_results = [r for r in results if r["best"]["trades"] >= 5]
    meets_target = [r for r in valid_results if r["best"]["winRate"] >= 50]

    # ── RANKING 1: Pure win rate (top10_wr) ──
    wr_sorted = sorted(valid_results, key=lambda r: (
        r["best"]["winRate"],
        r["best"].get("pf", 0),
        r["best"]["trades"],
    ), reverse=True)

    # ── RANKING 2: Sweet spot — ≥50% WR AND ≥20 trades, sorted by P&L ──
    sweet_spot = [r for r in valid_results
                  if r["best"]["winRate"] >= 50 and r["best"]["trades"] >= 20]
    sweet_spot.sort(key=lambda r: (
        r["best"].get("totalPnl", 0),
        r["best"]["winRate"],
        r["best"].get("pf", 0),
    ), reverse=True)

    # ── RANKING 3: Balanced — ≥50% WR AND ≥10 trades, scored by composite ──
    balanced = [r for r in valid_results
                if r["best"]["winRate"] >= 50 and r["best"]["trades"] >= 10]
    balanced.sort(key=lambda r: (
        r["best"]["winRate"] * 0.4 +
        min(r["best"]["trades"], 50) * 0.3 +
        r["best"].get("pf", 0) * 5 +
        max(0, r["best"].get("totalPnl", 0)) * 0.005
    ), reverse=True)

    baseline = {"trades": 64, "winRate": 31.2, "pf": 1.02, "totalPnl": 163.97}

    best_overall = sweet_spot[0] if sweet_spot else (balanced[0] if balanced else (wr_sorted[0] if wr_sorted else None))

    return {
        "totalCombinations": total,
        "totalValid": len(valid_results),
        "meetsTarget": len(meets_target),
        "sweetSpotCount": len(sweet_spot),
        "balancedCount": len(balanced),
        "elapsedSeconds": elapsed,
        "baseline": baseline,
        "top10": wr_sorted[:10],
        "top10_sweetspot": sweet_spot[:10],
        "top10_balanced": balanced[:10],
        "bestFound": best_overall,
        "candles5m": len(candles_5m),
        "candles15m": len(candles_15m),
    }
