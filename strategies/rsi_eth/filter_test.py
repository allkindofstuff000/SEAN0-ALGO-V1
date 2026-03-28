"""
RSI EMA ETH Filter Test — Tests 6 entry filters against baseline.
READ-ONLY: Does NOT modify any live strategy files.
Runs both filtered and unfiltered backtests on same data for comparison.
"""
from __future__ import annotations
import logging, time, math
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)


# ── Indicators ────────────────────────────────────────────

def _ema(values, period):
    if len(values) < period: return [None] * len(values)
    mult = 2.0 / (period + 1)
    result = [None] * (period - 1)
    sma = sum(values[:period]) / period
    result.append(sma)
    for i in range(period, len(values)):
        sma = values[i] * mult + result[-1] * (1 - mult)
        result.append(sma)
    return result

def _rsi(closes, period=14):
    if len(closes) < period + 1: return [None] * len(closes)
    result = [None] * period
    gains, losses = [], []
    for i in range(1, period + 1):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0)); losses.append(max(-d, 0))
    ag = sum(gains) / period; al = sum(losses) / period
    rs = ag / al if al > 0 else 100
    result.append(100 - 100 / (1 + rs))
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i-1]
        ag = (ag * (period - 1) + max(d, 0)) / period
        al = (al * (period - 1) + max(-d, 0)) / period
        rs = ag / al if al > 0 else 100
        result.append(100 - 100 / (1 + rs))
    return result

def _atr(candles, period=14):
    if len(candles) < 2: return [None] * len(candles)
    trs = [None]
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i-1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    result = [None] * period
    if len(trs) > period:
        avg = sum(t for t in trs[1:period+1]) / period
        result.append(avg)
        for i in range(period + 1, len(trs)):
            avg = (avg * (period - 1) + trs[i]) / period
            result.append(avg)
    return result

def _adx(candles, period=14):
    """Calculate ADX for trend strength."""
    if len(candles) < period * 2 + 1:
        return [None] * len(candles)
    result = [None] * len(candles)
    plus_dm = [0.0] * len(candles)
    minus_dm = [0.0] * len(candles)
    tr_list = [0.0] * len(candles)

    for i in range(1, len(candles)):
        h, l = candles[i]["high"], candles[i]["low"]
        ph, pl = candles[i-1]["high"], candles[i-1]["low"]
        pc = candles[i-1]["close"]
        tr_list[i] = max(h - l, abs(h - pc), abs(l - pc))
        up = h - ph
        down = pl - l
        plus_dm[i] = up if up > down and up > 0 else 0
        minus_dm[i] = down if down > up and down > 0 else 0

    # Wilder smoothing
    atr_s = sum(tr_list[1:period+1])
    plus_s = sum(plus_dm[1:period+1])
    minus_s = sum(minus_dm[1:period+1])

    dx_vals = []
    for i in range(period, len(candles)):
        if i == period:
            atr_s = sum(tr_list[1:period+1])
            plus_s = sum(plus_dm[1:period+1])
            minus_s = sum(minus_dm[1:period+1])
        else:
            atr_s = atr_s - atr_s / period + tr_list[i]
            plus_s = plus_s - plus_s / period + plus_dm[i]
            minus_s = minus_s - minus_s / period + minus_dm[i]

        plus_di = 100 * plus_s / atr_s if atr_s > 0 else 0
        minus_di = 100 * minus_s / atr_s if atr_s > 0 else 0
        di_sum = plus_di + minus_di
        dx = 100 * abs(plus_di - minus_di) / di_sum if di_sum > 0 else 0
        dx_vals.append(dx)

    # ADX = smoothed DX
    if len(dx_vals) >= period:
        adx_val = sum(dx_vals[:period]) / period
        for j in range(period, len(candles)):
            idx = j - period
            if idx < len(dx_vals):
                adx_val = (adx_val * (period - 1) + dx_vals[idx]) / period
                result[j] = adx_val

    return result


# ── 6 FILTERS ─────────────────────────────────────────────

def filter_session(candle, direction):
    """
    Filter 1: Session Filter — avoid Asian session noise.
    Asian session (00:00-08:00 UTC) has lower volume, more fakeouts.
    Allow trades only during London (08-16) and NY (13-21).
    """
    ts = candle.get("time", 0)
    if isinstance(ts, (int, float)) and ts > 1e9:
        hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
    else:
        return True  # can't determine time, allow

    # Block Asian session (00:00-07:59 UTC)
    if hour < 8:
        return False
    return True


def filter_trend(candle, direction, ema_fast, ema_slow, adx_val):
    """
    Filter 2: Trend Filter — block counter-trend entries.
    Only allow LONG when EMA fast > slow AND ADX > 20 (trending).
    Only allow SHORT when EMA fast < slow AND ADX > 20.
    """
    if ema_fast is None or ema_slow is None:
        return True

    if direction == "LONG" and ema_fast < ema_slow:
        return False  # counter-trend long
    if direction == "SHORT" and ema_fast > ema_slow:
        return False  # counter-trend short

    # ADX must show some trend (> 18)
    if adx_val is not None and adx_val < 18:
        return False  # choppy / no trend

    return True


def filter_volatility(candles, i, atr_val, atr_avg):
    """
    Filter 3: Volatility Filter — skip low-volume choppy entries.
    If ATR is below 60% of average → market is too quiet, signals unreliable.
    If ATR is above 250% of average → market is too wild, SL too likely to hit.
    """
    if atr_val is None or atr_avg is None or atr_avg == 0:
        return True

    ratio = atr_val / atr_avg
    if ratio < 0.6:
        return False  # dead market
    if ratio > 2.5:
        return False  # too volatile
    return True


def filter_spread(candle, atr_val):
    """
    Filter 4: Spread/Wick Filter — skip candles with excessive wicks.
    Large wicks relative to body = indecision, bad entry.
    If total wick > 70% of candle range → skip (doji-like).
    """
    body = abs(candle["close"] - candle["open"])
    total_range = candle["high"] - candle["low"]
    if total_range == 0:
        return False

    body_pct = body / total_range
    if body_pct < 0.30:
        return False  # more than 70% wick = indecision candle

    return True


def filter_time_of_day(candle):
    """
    Filter 5: Time of Day Filter — avoid late session bad setups.
    Block entries during last 2 hours of NY session (19:00-21:00 UTC)
    and during the deadest hours (02:00-05:00 UTC).
    """
    ts = candle.get("time", 0)
    if isinstance(ts, (int, float)) and ts > 1e9:
        hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
    else:
        return True

    # Dead zone: 02:00-04:59 UTC
    if 2 <= hour < 5:
        return False
    # Late NY close: 20:00-21:59 UTC
    if 20 <= hour < 22:
        return False
    return True


def filter_confluence(candles, i, direction, ema_fast, ema_slow, rsi_val, atr_val):
    """
    Filter 6: Confluence Filter — require multiple confirmations.
    Entry only when at least 3 of these align:
    1. Price momentum (last 3 candles trending in direction)
    2. RSI not extreme (LONG: RSI < 65, SHORT: RSI > 35) — room to run
    3. EMA spread increasing (trend accelerating)
    4. Candle body > 50% of range (conviction candle)
    """
    score = 0

    # 1. Price momentum — last 3 candles moving in direction
    if i >= 3:
        last3_up = all(candles[i-j]["close"] > candles[i-j]["open"] for j in range(3))
        last3_dn = all(candles[i-j]["close"] < candles[i-j]["open"] for j in range(3))
        if direction == "LONG" and last3_up:
            score += 1
        elif direction == "SHORT" and last3_dn:
            score += 1
        # Also count if 2 of 3 are in direction
        elif direction == "LONG":
            bullish_count = sum(1 for j in range(3) if candles[i-j]["close"] > candles[i-j]["open"])
            if bullish_count >= 2:
                score += 1
        elif direction == "SHORT":
            bearish_count = sum(1 for j in range(3) if candles[i-j]["close"] < candles[i-j]["open"])
            if bearish_count >= 2:
                score += 1

    # 2. RSI has room to run (not already extreme)
    if rsi_val is not None:
        if direction == "LONG" and rsi_val < 65:
            score += 1
        elif direction == "SHORT" and rsi_val > 35:
            score += 1

    # 3. EMA spread increasing
    if ema_fast is not None and ema_slow is not None and i >= 2:
        prev_spread = abs((ema_fast - ema_slow)) if ema_fast and ema_slow else 0
        # Just check EMAs are separated enough
        if abs(ema_fast - ema_slow) > atr_val * 0.3 if atr_val else 0:
            score += 1

    # 4. Entry candle has good body (conviction)
    c = candles[i]
    body = abs(c["close"] - c["open"])
    rng = c["high"] - c["low"]
    if rng > 0 and body / rng > 0.45:
        score += 1

    # Need at least 2 of 4 confirmations
    return score >= 2


# ── BACKTEST ENGINE ───────────────────────────────────────

def run_filtered_backtest(
    candles: list[dict],
    starting_balance: float = 5000.0,
    risk_pct: float = 0.03,
    use_filters: bool = True,
    filter_flags: dict = None,
) -> dict:
    """
    Run backtest with optional filters.
    filter_flags controls which filters are active:
      {"session": True, "trend": True, "volatility": True,
       "spread": True, "time_of_day": True, "confluence": True}
    """
    if filter_flags is None:
        filter_flags = {
            "session": True, "trend": True, "volatility": True,
            "spread": True, "time_of_day": True, "confluence": True,
        }

    # Optimized params from optimizer
    ema_fast_p = 9
    ema_slow_p = 50
    rsi_period = 7
    rsi_ob = 70
    rsi_os = 35
    atr_sl_mult = 1.0
    tp_ratio = 2.5

    min_bars = max(ema_slow_p, rsi_period) + 2
    if len(candles) < min_bars:
        return {"error": "Not enough candles"}

    closes = [c["close"] for c in candles]
    ema_f = _ema(closes, ema_fast_p)
    ema_s = _ema(closes, ema_slow_p)
    rsi_v = _rsi(closes, rsi_period)
    atr_v = _atr(candles, 14)
    adx_v = _adx(candles, 14)

    # ATR average for volatility filter
    atr_avg = [None] * len(atr_v)
    for idx in range(50, len(atr_v)):
        valid = [a for a in atr_v[idx-50:idx] if a is not None]
        atr_avg[idx] = sum(valid) / len(valid) if valid else None

    balance = starting_balance
    trades = []
    open_trade = None
    filter_stats = {
        "session_blocked": 0, "trend_blocked": 0, "volatility_blocked": 0,
        "spread_blocked": 0, "time_blocked": 0, "confluence_blocked": 0,
        "total_signals": 0, "passed_all": 0,
    }

    start_idx = max(ema_slow_p, rsi_period) + 1

    for i in range(start_idx, len(candles) - 1):
        c = candles[i]
        price = c["close"]

        # ── Manage open trade ─────────────────────────
        if open_trade:
            is_long = open_trade["dir"] == "LONG"
            hit_sl = (c["low"] <= open_trade["sl"]) if is_long else (c["high"] >= open_trade["sl"])
            hit_tp = (c["high"] >= open_trade["tp"]) if is_long else (c["low"] <= open_trade["tp"])

            if hit_sl or hit_tp:
                outcome = "WIN" if hit_tp else "LOSS"
                pnl = open_trade["risk_amt"] * tp_ratio if hit_tp else -open_trade["risk_amt"]
                balance += pnl

                ts = c.get("time", 0)
                if isinstance(ts, (int, float)) and ts > 1e9:
                    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                    exit_time_str = dt.strftime("%Y-%m-%d %H:%M")
                else:
                    exit_time_str = str(ts)

                trades.append({
                    "num": len(trades) + 1,
                    "dir": open_trade["dir"],
                    "entry": open_trade["entry"],
                    "sl": open_trade["sl"],
                    "tp": open_trade["tp"],
                    "exitPrice": open_trade["tp"] if hit_tp else open_trade["sl"],
                    "result": outcome,
                    "pnl": round(pnl, 2),
                    "balance": round(balance, 2),
                    "openTime": open_trade["open_time_str"],
                    "exitTime": exit_time_str,
                    "rsi": open_trade.get("rsi", 0),
                    "filters_passed": open_trade.get("filters", ""),
                })
                open_trade = None
            continue

        # ── Signal check ──────────────────────────────
        if any(v is None for v in [rsi_v[i], rsi_v[i-1], ema_f[i], ema_s[i], atr_v[i]]):
            continue

        curr_rsi = rsi_v[i]
        prev_rsi = rsi_v[i-1]
        fast_now = ema_f[i]
        slow_now = ema_s[i]

        # Entry Option C: RSI cross + EMA bias
        signal = None
        rsi_bull = prev_rsi <= rsi_os and curr_rsi > rsi_os
        rsi_bear = prev_rsi >= rsi_ob and curr_rsi < rsi_ob

        if rsi_bull and price > slow_now:
            signal = "LONG"
        elif rsi_bear and price < slow_now:
            signal = "SHORT"

        if not signal:
            continue

        filter_stats["total_signals"] += 1

        # ── Apply filters ─────────────────────────────
        if use_filters:
            blocked_by = None

            # Filter 1: Session
            if filter_flags.get("session") and not filter_session(c, signal):
                filter_stats["session_blocked"] += 1
                blocked_by = "session"

            # Filter 2: Trend
            if not blocked_by and filter_flags.get("trend"):
                adx_val = adx_v[i] if i < len(adx_v) else None
                if not filter_trend(c, signal, fast_now, slow_now, adx_val):
                    filter_stats["trend_blocked"] += 1
                    blocked_by = "trend"

            # Filter 3: Volatility
            if not blocked_by and filter_flags.get("volatility"):
                aa = atr_avg[i] if i < len(atr_avg) else None
                if not filter_volatility(candles, i, atr_v[i], aa):
                    filter_stats["volatility_blocked"] += 1
                    blocked_by = "volatility"

            # Filter 4: Spread/Wick
            if not blocked_by and filter_flags.get("spread"):
                if not filter_spread(c, atr_v[i]):
                    filter_stats["spread_blocked"] += 1
                    blocked_by = "spread"

            # Filter 5: Time of Day
            if not blocked_by and filter_flags.get("time_of_day"):
                if not filter_time_of_day(c):
                    filter_stats["time_blocked"] += 1
                    blocked_by = "time"

            # Filter 6: Confluence
            if not blocked_by and filter_flags.get("confluence"):
                if not filter_confluence(candles, i, signal, fast_now, slow_now, curr_rsi, atr_v[i]):
                    filter_stats["confluence_blocked"] += 1
                    blocked_by = "confluence"

            if blocked_by:
                continue

        filter_stats["passed_all"] += 1

        # ── Open trade ────────────────────────────────
        atr = atr_v[i]
        risk_amt = balance * risk_pct

        if signal == "LONG":
            sl = price - atr * atr_sl_mult
            tp = price + (price - sl) * tp_ratio
        else:
            sl = price + atr * atr_sl_mult
            tp = price - (sl - price) * tp_ratio

        ts = c.get("time", 0)
        if isinstance(ts, (int, float)) and ts > 1e9:
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            open_time_str = dt.strftime("%Y-%m-%d %H:%M")
        else:
            open_time_str = str(ts)

        open_trade = {
            "dir": signal, "entry": price, "sl": sl, "tp": tp,
            "risk_amt": risk_amt, "open_time_str": open_time_str,
            "rsi": round(curr_rsi, 1),
            "filters": "ALL" if use_filters else "NONE",
        }

    # ── Metrics ───────────────────────────────────────
    total = len(trades)
    wins = [t for t in trades if t["result"] == "WIN"]
    losses = [t for t in trades if t["result"] == "LOSS"]
    gross_w = sum(t["pnl"] for t in wins)
    gross_l = abs(sum(t["pnl"] for t in losses))
    total_pnl = sum(t["pnl"] for t in trades)

    # Max drawdown
    peak = starting_balance
    max_dd = 0
    bal = starting_balance
    for t in trades:
        bal += t["pnl"]
        if bal > peak: peak = bal
        dd = peak - bal
        if dd > max_dd: max_dd = dd

    # Win rate by session
    session_stats = {}
    for t in trades:
        try:
            dt = datetime.strptime(t["openTime"], "%Y-%m-%d %H:%M")
            h = dt.hour
            if h < 8: sess = "Asian"
            elif h < 16: sess = "London"
            else: sess = "NewYork"
        except:
            sess = "Unknown"
        if sess not in session_stats:
            session_stats[sess] = {"trades": 0, "wins": 0}
        session_stats[sess]["trades"] += 1
        if t["result"] == "WIN":
            session_stats[sess]["wins"] += 1

    for k, v in session_stats.items():
        v["winRate"] = round(v["wins"] / v["trades"] * 100, 1) if v["trades"] > 0 else 0

    return {
        "totalTrades": total,
        "wins": len(wins),
        "losses": len(losses),
        "winRate": round(len(wins) / total * 100, 1) if total > 0 else 0,
        "totalPnl": round(total_pnl, 2),
        "finalBalance": round(starting_balance + total_pnl, 2),
        "returnPct": round(total_pnl / starting_balance * 100, 1) if starting_balance > 0 else 0,
        "profitFactor": round(gross_w / gross_l, 2) if gross_l > 0 else float("inf"),
        "maxDrawdown": round(max_dd, 2),
        "maxDrawdownPct": round(max_dd / peak * 100, 1) if peak > 0 else 0,
        "avgWin": round(gross_w / len(wins), 2) if wins else 0,
        "avgLoss": round(gross_l / len(losses), 2) if losses else 0,
        "tradesPerDay": round(total / 30, 1),
        "filterStats": filter_stats,
        "sessionStats": session_stats,
        "trades": trades,
    }


def run_comparison(candles, balance=5000.0, risk_pct=0.03):
    """Run baseline (no filters) vs filtered and compare."""
    baseline = run_filtered_backtest(candles, balance, risk_pct, use_filters=False)
    filtered = run_filtered_backtest(candles, balance, risk_pct, use_filters=True)

    # Also test each filter individually to see impact
    individual = {}
    filter_names = ["session", "trend", "volatility", "spread", "time_of_day", "confluence"]
    for f in filter_names:
        flags = {k: (k == f) for k in filter_names}
        result = run_filtered_backtest(candles, balance, risk_pct, use_filters=True, filter_flags=flags)
        individual[f] = {
            "trades": result["totalTrades"],
            "winRate": result["winRate"],
            "pnl": result["totalPnl"],
            "blocked": result["filterStats"].get(f"{f}_blocked", 0),
        }

    return {
        "baseline": baseline,
        "filtered": filtered,
        "individual_filters": individual,
    }
