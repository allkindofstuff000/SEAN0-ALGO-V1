"""
Minimal ICT Strategy Backtest Engine — ETH/USDT
Tests 6 ICT variants on 5M data with 15M bias.
Returns per-variant metrics and picks the best.
"""
from __future__ import annotations
import math
import time as _time
from typing import Any

# ── Indicator helpers ─────────────────────────────────────────────────────────

def _ema(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1)
    s = sum(values[:period]) / period
    out[period - 1] = s
    for i in range(period, len(values)):
        s = values[i] * k + s * (1 - k)
        out[i] = s
    return out


def _atr(candles: list[dict], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(candles)
    if len(candles) < period + 1:
        return out
    trs: list[float] = []
    for i in range(1, len(candles)):
        c = candles[i]
        p = candles[i - 1]
        tr = max(c["high"] - c["low"], abs(c["high"] - p["close"]), abs(c["low"] - p["close"]))
        trs.append(tr)
    if len(trs) < period:
        return out
    atr_val = sum(trs[:period]) / period
    out[period] = atr_val
    for i in range(period, len(trs)):
        atr_val = (atr_val * (period - 1) + trs[i]) / period
        out[i + 1] = atr_val
    return out


def _vwap_weekly(candles: list[dict]) -> list[dict | None]:
    """Weekly-anchored VWAP with std dev bands."""
    out: list[dict | None] = [None] * len(candles)
    cum_vol = cum_tpv = cum_tpv2 = 0.0
    last_week = -1
    for i, c in enumerate(candles):
        from datetime import datetime, timezone
        dt = datetime.fromtimestamp(c["time"], tz=timezone.utc)
        week = dt.isocalendar()[1]
        if week != last_week:
            cum_vol = cum_tpv = cum_tpv2 = 0.0
            last_week = week
        tp = (c["high"] + c["low"] + c["close"]) / 3.0
        cum_vol += c["volume"]
        cum_tpv += tp * c["volume"]
        cum_tpv2 += tp * tp * c["volume"]
        if cum_vol > 0:
            vwap = cum_tpv / cum_vol
            var = max(0, (cum_tpv2 / cum_vol) - vwap * vwap)
            sd = math.sqrt(var)
            out[i] = {"vwap": vwap, "upper1": vwap + sd, "lower1": vwap - sd,
                       "upper2": vwap + 2 * sd, "lower2": vwap - 2 * sd}
    return out


# ── ICT Structure helpers ─────────────────────────────────────────────────────

def _find_swings(candles: list[dict], pivot_bars: int = 3) -> list[dict]:
    """Find swing highs and lows. Mark BOS when a swing is broken."""
    swings: list[dict] = []
    for i in range(pivot_bars, len(candles) - pivot_bars):
        # Swing high
        is_high = all(candles[i]["high"] >= candles[i + d]["high"] for d in range(-pivot_bars, pivot_bars + 1) if d != 0)
        if is_high:
            swings.append({"index": i, "price": candles[i]["high"], "type": "high", "isBOS": False})
        # Swing low
        is_low = all(candles[i]["low"] <= candles[i + d]["low"] for d in range(-pivot_bars, pivot_bars + 1) if d != 0)
        if is_low:
            swings.append({"index": i, "price": candles[i]["low"], "type": "low", "isBOS": False})

    # Mark BOS: when price closes beyond a swing level
    for si in range(len(swings)):
        sw = swings[si]
        for j in range(sw["index"] + 1, min(sw["index"] + 30, len(candles))):
            if sw["type"] == "high" and candles[j]["close"] > sw["price"]:
                swings[si] = {**sw, "isBOS": True, "bosType": "bullish", "bosIndex": j}
                break
            if sw["type"] == "low" and candles[j]["close"] < sw["price"]:
                swings[si] = {**sw, "isBOS": True, "bosType": "bearish", "bosIndex": j}
                break
    return swings


def _find_fvgs(candles: list[dict], min_size: float = 0.30) -> list[dict]:
    """Find Fair Value Gaps on 5M candles."""
    fvgs: list[dict] = []
    for i in range(2, len(candles)):
        c0, c1, c2 = candles[i - 2], candles[i - 1], candles[i]
        # Bullish FVG: gap between c0 high and c2 low
        if c2["low"] > c0["high"] and (c2["low"] - c0["high"]) >= min_size:
            fvgs.append({
                "type": "bullish", "high": c2["low"], "low": c0["high"],
                "formedIndex": i, "mitigated": False, "midpoint": (c2["low"] + c0["high"]) / 2,
            })
        # Bearish FVG: gap between c0 low and c2 high
        if c0["low"] > c2["high"] and (c0["low"] - c2["high"]) >= min_size:
            fvgs.append({
                "type": "bearish", "high": c0["low"], "low": c2["high"],
                "formedIndex": i, "mitigated": False, "midpoint": (c0["low"] + c2["high"]) / 2,
            })
    return fvgs


def _find_obs(candles: list[dict]) -> list[dict]:
    """Find Order Blocks — last opposite candle before impulse move."""
    obs: list[dict] = []
    for i in range(3, len(candles)):
        # Bullish OB: bearish candle followed by 3 bullish candles
        if candles[i - 3]["close"] < candles[i - 3]["open"]:  # bearish
            bullish_count = sum(1 for k in range(i - 2, i + 1)
                                if k < len(candles) and candles[k]["close"] > candles[k]["open"])
            if bullish_count >= 2:
                ob = candles[i - 3]
                obs.append({
                    "type": "bullish", "high": ob["high"], "low": ob["low"],
                    "formedIndex": i - 3, "mitigated": False,
                })
        # Bearish OB: bullish candle followed by 3 bearish candles
        if candles[i - 3]["close"] > candles[i - 3]["open"]:  # bullish
            bearish_count = sum(1 for k in range(i - 2, i + 1)
                                if k < len(candles) and candles[k]["close"] < candles[k]["open"])
            if bearish_count >= 2:
                ob = candles[i - 3]
                obs.append({
                    "type": "bearish", "high": ob["high"], "low": ob["low"],
                    "formedIndex": i - 3, "mitigated": False,
                })
    return obs


def _find_ssl_pools(candles: list[dict], tolerance: float = 0.20) -> list[dict]:
    """Find equal lows/highs (liquidity pools)."""
    pools: list[dict] = []
    lows = [(i, c["low"]) for i, c in enumerate(candles)]
    for i in range(len(lows)):
        matches = [j for j in range(max(0, i - 25), i) if abs(lows[i][1] - lows[j][1]) <= tolerance]
        if len(matches) >= 1:
            avg_price = (lows[i][1] + sum(lows[j][1] for j in matches)) / (len(matches) + 1)
            if not any(abs(p["price"] - avg_price) < tolerance * 2 for p in pools):
                pools.append({"type": "ssl", "price": avg_price, "index": i, "swept": False})
    return pools


# ── FVG/OB mitigation tracker ─────────────────────────────────────────────────

def _update_mitigations(candle: dict, fvgs: list[dict], obs: list[dict]) -> None:
    """Mark FVGs and OBs as mitigated when price closes through them."""
    for f in fvgs:
        if f["mitigated"]:
            continue
        if f["type"] == "bullish" and candle["close"] < f["low"]:
            f["mitigated"] = True
        elif f["type"] == "bearish" and candle["close"] > f["high"]:
            f["mitigated"] = True
    for o in obs:
        if o["mitigated"]:
            continue
        if o["type"] == "bullish" and candle["close"] < o["low"]:
            o["mitigated"] = True
        elif o["type"] == "bearish" and candle["close"] > o["high"]:
            o["mitigated"] = True


# ── Trade management ──────────────────────────────────────────────────────────

def _manage_trade(trade: dict, candle: dict) -> dict | None:
    """Check if open trade hits SL/TP/BE/time exit. Returns closed trade or None."""
    trade["barsSinceOpen"] = trade.get("barsSinceOpen", 0) + 1
    d = trade["direction"]

    if d == "LONG":
        # SL hit
        if candle["low"] <= trade["sl"]:
            return {**trade, "exitPrice": trade["sl"], "exitReason": "SL_HIT",
                    "pnlR": 0.5 if trade.get("tp1Hit") else -1.0, "closeTime": candle["time"]}
        # TP1 hit
        if not trade.get("tp1Hit") and candle["high"] >= trade["tp1"]:
            trade["tp1Hit"] = True
            trade["sl"] = trade["entry"]  # break even
        # TP2 hit
        if trade.get("tp1Hit") and candle["high"] >= trade["tp2"]:
            return {**trade, "exitPrice": trade["tp2"], "exitReason": "TP2_HIT",
                    "pnlR": 2.5, "closeTime": candle["time"]}
        # BE hit after TP1
        if trade.get("tp1Hit") and candle["low"] <= trade["entry"]:
            return {**trade, "exitPrice": trade["entry"], "exitReason": "BE_HIT",
                    "pnlR": 0.5, "closeTime": candle["time"]}
    else:  # SHORT
        if candle["high"] >= trade["sl"]:
            return {**trade, "exitPrice": trade["sl"], "exitReason": "SL_HIT",
                    "pnlR": 0.5 if trade.get("tp1Hit") else -1.0, "closeTime": candle["time"]}
        if not trade.get("tp1Hit") and candle["low"] <= trade["tp1"]:
            trade["tp1Hit"] = True
            trade["sl"] = trade["entry"]
        if trade.get("tp1Hit") and candle["low"] <= trade["tp2"]:
            return {**trade, "exitPrice": trade["tp2"], "exitReason": "TP2_HIT",
                    "pnlR": 2.5, "closeTime": candle["time"]}
        if trade.get("tp1Hit") and candle["high"] >= trade["entry"]:
            return {**trade, "exitPrice": trade["entry"], "exitReason": "BE_HIT",
                    "pnlR": 0.5, "closeTime": candle["time"]}

    # Time exit (2 hours = 24 x 5min bars)
    if trade["barsSinceOpen"] >= 24:
        ep = candle["close"]
        risk = abs(trade["entry"] - trade.get("originalSl", trade["sl"]))
        pnl_r = (ep - trade["entry"]) / risk if d == "LONG" and risk > 0 else (trade["entry"] - ep) / risk if risk > 0 else 0
        if trade.get("tp1Hit"):
            pnl_r = max(pnl_r, 0.5)
        return {**trade, "exitPrice": ep, "exitReason": "TIME_EXIT", "pnlR": round(pnl_r, 2), "closeTime": candle["time"]}

    return None


# ── Get corresponding 15M index for a 5M timestamp ───────────────────────────

def _get_m15_idx(ts: int, m15: list[dict]) -> int:
    """Binary search for the 15M candle that contains this 5M timestamp."""
    lo, hi = 0, len(m15) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if m15[mid]["time"] <= ts:
            lo = mid + 1
        else:
            hi = mid - 1
    return max(0, hi)


# ── Variant signal checkers ───────────────────────────────────────────────────

def _check_A(candle, i, m15, ema200_15m, fvgs):
    """FVG Retest."""
    price = candle["close"]
    mi = _get_m15_idx(candle["time"], m15)
    ema = ema200_15m[mi] if mi < len(ema200_15m) else None
    if not ema:
        return None
    bias = "bull" if price > ema else "bear"
    ftype = "bullish" if bias == "bull" else "bearish"
    candidates = [f for f in fvgs if f["type"] == ftype and f["formedIndex"] < i
                  and not f["mitigated"] and i - f["formedIndex"] < 60
                  and candle["close"] >= f["low"] and candle["close"] <= f["high"]]
    if not candidates:
        return None
    fvg = candidates[-1]
    mid = (fvg["high"] + fvg["low"]) / 2
    if bias == "bull" and candle["close"] < mid:
        return None
    if bias == "bear" and candle["close"] > mid:
        return None
    sl = fvg["low"] - 0.20 if bias == "bull" else fvg["high"] + 0.20
    risk = abs(price - sl)
    if risk < 0.10:
        return None
    sign = 1 if bias == "bull" else -1
    return {"direction": "LONG" if bias == "bull" else "SHORT", "entry": price, "sl": sl,
            "tp1": price + sign * risk * 2, "tp2": price + sign * risk * 3, "signal": "FVG_RETEST"}


def _check_B(candle, i, m15, ema200_15m, obs):
    """OB Touch + Close."""
    price = candle["close"]
    mi = _get_m15_idx(candle["time"], m15)
    ema = ema200_15m[mi] if mi < len(ema200_15m) else None
    if not ema:
        return None
    bias = "bull" if price > ema else "bear"
    otype = "bullish" if bias == "bull" else "bearish"
    candidates = [o for o in obs if o["type"] == otype and o["formedIndex"] < i
                  and i - o["formedIndex"] < 60 and not o["mitigated"]
                  and abs(price - (o["high"] + o["low"]) / 2) / max(price, 1) < 0.003]
    if not candidates:
        return None
    ob = candidates[-1]
    if bias == "bull":
        if candle["close"] < ob["low"]:
            return None
        body = abs(candle["close"] - candle["open"])
        rng = candle["high"] - candle["low"]
        if rng > 0 and body / rng < 0.35:
            return None
        sl = ob["low"] - 0.30
    else:
        if candle["close"] > ob["high"]:
            return None
        body = abs(candle["close"] - candle["open"])
        rng = candle["high"] - candle["low"]
        if rng > 0 and body / rng < 0.35:
            return None
        sl = ob["high"] + 0.30
    risk = abs(price - sl)
    if risk < 0.10:
        return None
    sign = 1 if bias == "bull" else -1
    return {"direction": "LONG" if bias == "bull" else "SHORT", "entry": price, "sl": sl,
            "tp1": price + sign * risk * 2, "tp2": price + sign * risk * 3, "signal": "OB_TOUCH"}


def _check_C(candle, i, m15, ema200_15m, ssl_pools):
    """SSL Sweep + Recovery (long bias only)."""
    price = candle["close"]
    mi = _get_m15_idx(candle["time"], m15)
    ema = ema200_15m[mi] if mi < len(ema200_15m) else None
    if not ema or price < ema:
        return None
    candidates = [p for p in ssl_pools if not p["swept"] and p["index"] < i
                  and candle["low"] < p["price"] and candle["close"] > p["price"]]
    for p in candidates:
        depth = p["price"] - candle["low"]
        if 0.10 <= depth <= 0.80:
            p["swept"] = True
            sl = candle["low"] - 0.20
            risk = price - sl
            if risk < 0.10:
                continue
            return {"direction": "LONG", "entry": price, "sl": sl,
                    "tp1": price + risk * 2, "tp2": price + risk * 3, "signal": "SSL_SWEEP"}
    return None


def _check_D(candle, i, m15, ema200_15m, fvgs, ssl_pools):
    """FVG + Sweep Combo."""
    price = candle["close"]
    mi = _get_m15_idx(candle["time"], m15)
    ema = ema200_15m[mi] if mi < len(ema200_15m) else None
    if not ema:
        return None
    bias = "bull" if price > ema else "bear"
    ftype = "bullish" if bias == "bull" else "bearish"
    candidates = [f for f in fvgs if f["type"] == ftype and f["formedIndex"] < i
                  and not f["mitigated"] and candle["low"] <= f["high"] and candle["high"] >= f["low"]]
    if not candidates:
        return None
    fvg = candidates[-1]
    if bias == "bull":
        if not (candle["low"] < fvg["low"] and candle["close"] > fvg["low"]):
            return None
        sl = candle["low"] - 0.20
    else:
        if not (candle["high"] > fvg["high"] and candle["close"] < fvg["high"]):
            return None
        sl = candle["high"] + 0.20
    risk = abs(price - sl)
    if risk < 0.10:
        return None
    sign = 1 if bias == "bull" else -1
    return {"direction": "LONG" if bias == "bull" else "SHORT", "entry": price, "sl": sl,
            "tp1": price + sign * risk * 2, "tp2": price + sign * risk * 3, "signal": "FVG_SWEEP"}


def _check_E(candle, i, swings):
    """BOS Retest."""
    recent = [s for s in swings if s.get("isBOS") and s.get("bosIndex")
              and i - s["bosIndex"] < 15 and s["bosIndex"] < i]
    if not recent:
        return None
    bos = recent[-1]
    level = bos["price"]
    bias = "bull" if bos.get("bosType") == "bullish" else "bear"
    near = abs(candle["low"] - level) < 0.30 or (candle["low"] <= level and candle["close"] > level)
    if bias == "bear":
        near = abs(candle["high"] - level) < 0.30 or (candle["high"] >= level and candle["close"] < level)
    if not near:
        return None
    if bias == "bull" and candle["close"] <= level:
        return None
    if bias == "bear" and candle["close"] >= level:
        return None
    sl = level - 0.30 if bias == "bull" else level + 0.30
    risk = abs(candle["close"] - sl)
    if risk < 0.10:
        return None
    sign = 1 if bias == "bull" else -1
    return {"direction": "LONG" if bias == "bull" else "SHORT", "entry": candle["close"], "sl": sl,
            "tp1": candle["close"] + sign * risk * 2, "tp2": candle["close"] + sign * risk * 3, "signal": "BOS_RETEST"}


def _check_F(candle, i, vwap_data, fvgs):
    """VWAP + FVG Confluence."""
    if i >= len(vwap_data) or not vwap_data[i]:
        return None
    vwap = vwap_data[i]["vwap"]
    price = candle["close"]
    bias = "bull" if price < vwap else "bear"
    ftype = "bullish" if bias == "bull" else "bearish"
    candidates = [f for f in fvgs if f["type"] == ftype and f["formedIndex"] < i
                  and not f["mitigated"]
                  and abs((f["high"] + f["low"]) / 2 - vwap) < 1.0
                  and price >= f["low"] and price <= f["high"] * 1.002]
    if not candidates:
        return None
    fvg = candidates[-1]
    if abs(price - vwap) > 0.50:
        return None
    sl = fvg["low"] - 0.20 if bias == "bull" else fvg["high"] + 0.20
    risk = abs(price - sl)
    if risk < 0.10:
        return None
    sign = 1 if bias == "bull" else -1
    tp1 = price + sign * risk * 2
    tp2 = vwap_data[i]["upper1"] if bias == "bull" else vwap_data[i]["lower1"]
    return {"direction": "LONG" if bias == "bull" else "SHORT", "entry": price, "sl": sl,
            "tp1": tp1, "tp2": tp2, "signal": "VWAP_FVG"}


# ── Single variant runner ─────────────────────────────────────────────────────

def _run_variant(variant_id: str, m5: list[dict], m15: list[dict],
                 ema200_15m, fvgs, obs, ssl_pools, swings, vwap_data) -> dict:
    """Run one variant through all 5M candles."""
    trades: list[dict] = []
    open_trade: dict | None = None
    last_trade_time = 0
    trades_today = 0
    current_day = ""

    for i in range(200, len(m5)):
        candle = m5[i]
        _update_mitigations(candle, fvgs, obs)

        from datetime import datetime, timezone
        day = datetime.fromtimestamp(candle["time"], tz=timezone.utc).strftime("%Y-%m-%d")
        if day != current_day:
            current_day = day
            trades_today = 0

        # Manage open trade
        if open_trade is not None:
            result = _manage_trade(open_trade, candle)
            if result is not None:
                trades.append(result)
                open_trade = None
            continue

        # Rate limits
        if trades_today >= 5:
            continue
        if candle["time"] - last_trade_time < 900:
            continue

        # Check signal
        sig = None
        if variant_id == "A":
            sig = _check_A(candle, i, m15, ema200_15m, fvgs)
        elif variant_id == "B":
            sig = _check_B(candle, i, m15, ema200_15m, obs)
        elif variant_id == "C":
            sig = _check_C(candle, i, m15, ema200_15m, ssl_pools)
        elif variant_id == "D":
            sig = _check_D(candle, i, m15, ema200_15m, fvgs, ssl_pools)
        elif variant_id == "E":
            sig = _check_E(candle, i, swings)
        elif variant_id == "F":
            sig = _check_F(candle, i, vwap_data, fvgs)

        if sig is None:
            continue

        # Validate R:R
        risk = abs(sig["entry"] - sig["sl"])
        if risk < 0.01:
            continue
        rr = abs(sig["tp1"] - sig["entry"]) / risk
        if rr < 1.8:
            continue

        open_trade = {
            **sig, "variant": variant_id, "openTime": candle["time"],
            "openIndex": i, "tp1Hit": False, "barsSinceOpen": 0,
            "originalSl": sig["sl"],
        }
        trades_today += 1
        last_trade_time = candle["time"]

    # Close remaining
    if open_trade is not None:
        ep = m5[-1]["close"]
        risk = abs(open_trade["entry"] - open_trade["originalSl"])
        d = open_trade["direction"]
        pnl = ((ep - open_trade["entry"]) / risk if d == "LONG" else (open_trade["entry"] - ep) / risk) if risk > 0 else 0
        trades.append({**open_trade, "exitPrice": ep, "exitReason": "END_OF_TEST",
                       "pnlR": round(pnl, 2), "closeTime": m5[-1]["time"]})

    return _calc_metrics(variant_id, trades, m5)


def _calc_metrics(variant_id: str, trades: list[dict], m5: list[dict]) -> dict:
    """Calculate comprehensive metrics for a variant."""
    names = {"A": "FVG Retest", "B": "OB Touch", "C": "SSL Sweep",
             "D": "FVG+Sweep", "E": "BOS Retest", "F": "VWAP+FVG"}

    if not trades:
        return {"id": variant_id, "name": names.get(variant_id, variant_id),
                "winRate": 0, "tradesPerDay": 0, "avgRR": 0, "profitFactor": 0,
                "maxDrawdownR": 0, "totalPnLR": 0, "totalTrades": 0,
                "winners": 0, "losers": 0, "trades": [], "equityCurve": [],
                "bySession": {}, "meetsTarget": False}

    winners = [t for t in trades if t.get("pnlR", 0) > 0]
    losers = [t for t in trades if t.get("pnlR", 0) <= 0]
    win_rate = len(winners) / len(trades) * 100

    from datetime import datetime, timezone
    day_map: dict[str, int] = {}
    for t in trades:
        d = datetime.fromtimestamp(t["openTime"], tz=timezone.utc).strftime("%Y-%m-%d")
        day_map[d] = day_map.get(d, 0) + 1
    trades_per_day = sum(day_map.values()) / max(len(day_map), 1)

    avg_rr = sum(t["pnlR"] for t in winners) / max(len(winners), 1)
    gross_win = sum(t["pnlR"] for t in winners)
    gross_loss = abs(sum(t["pnlR"] for t in losers))
    pf = gross_win / gross_loss if gross_loss > 0 else gross_win if gross_win > 0 else 0

    cum_r = 0.0
    equity_curve = []
    for t in trades:
        cum_r += t.get("pnlR", 0)
        equity_curve.append({"time": t.get("closeTime", 0), "value": round(cum_r, 2)})

    peak = max_dd = run = 0.0
    for t in trades:
        run += t.get("pnlR", 0)
        if run > peak:
            peak = run
        dd = peak - run
        if dd > max_dd:
            max_dd = dd

    # By session
    by_session: dict[str, dict] = {}
    for sname, (h_start, h_end) in [("london", (8, 16)), ("ny", (13, 21)), ("asian", (0, 8))]:
        st = [t for t in trades
              if h_start <= datetime.fromtimestamp(t["openTime"], tz=timezone.utc).hour < h_end]
        if st:
            sw = [t for t in st if t.get("pnlR", 0) > 0]
            by_session[sname] = {"trades": len(st), "winRate": round(len(sw) / len(st) * 100, 1)}
        else:
            by_session[sname] = {"trades": 0, "winRate": 0}

    # Serializable trades
    safe_trades = []
    for t in trades:
        safe_trades.append({
            "variant": t.get("variant"), "direction": t.get("direction"),
            "entry": round(t.get("entry", 0), 2), "sl": round(t.get("sl", 0), 2),
            "tp1": round(t.get("tp1", 0), 2), "tp2": round(t.get("tp2", 0), 2),
            "exitPrice": round(t.get("exitPrice", 0), 2),
            "exitReason": t.get("exitReason", ""),
            "pnlR": round(t.get("pnlR", 0), 2),
            "signal": t.get("signal", ""),
            "openTime": t.get("openTime", 0), "closeTime": t.get("closeTime", 0),
        })

    return {
        "id": variant_id, "name": names.get(variant_id, variant_id),
        "winRate": round(win_rate, 1),
        "tradesPerDay": round(trades_per_day, 1),
        "avgRR": round(avg_rr, 2),
        "profitFactor": round(pf, 2),
        "maxDrawdownR": round(max_dd, 2),
        "totalPnLR": round(cum_r, 2),
        "totalTrades": len(trades),
        "winners": len(winners),
        "losers": len(losers),
        "trades": safe_trades,
        "equityCurve": equity_curve,
        "bySession": by_session,
        "meetsTarget": win_rate >= 50 and trades_per_day >= 4,
    }


# ── Main entry point ──────────────────────────────────────────────────────────

def run_all_variants(m5: list[dict], m15: list[dict]) -> dict[str, Any]:
    """
    Run all 6 ICT variants on the provided candle data.
    Returns { variants: {A..F}, best, recommendation }.
    """
    if len(m5) < 300:
        return {"error": "Need at least 300 5M candles", "variants": {}, "best": None}

    closes_15m = [c["close"] for c in m15]
    ema200_15m = _ema(closes_15m, 200)

    fvgs = _find_fvgs(m5, 0.30)
    obs = _find_obs(m5)
    ssl_pools = _find_ssl_pools(m5, 0.20)
    swings = _find_swings(m5, 3)
    vwap_data = _vwap_weekly(m5)

    variants: dict[str, dict] = {}
    for vid in ("A", "B", "C", "D", "E", "F"):
        # Deep-copy mutable state for each variant
        import copy
        v_fvgs = copy.deepcopy(fvgs)
        v_obs = copy.deepcopy(obs)
        v_ssl = copy.deepcopy(ssl_pools)
        variants[vid] = _run_variant(vid, m5, m15, ema200_15m, v_fvgs, v_obs, v_ssl, swings, vwap_data)

    # Find best
    scored = sorted(
        [v for v in variants.values() if v["meetsTarget"]],
        key=lambda v: (v["winRate"] * 0.5 + min(v["tradesPerDay"], 8) * 2 + v["avgRR"] * 5 + v["profitFactor"] * 3),
        reverse=True,
    )
    best = scored[0] if scored else None
    rec = ""
    if best:
        rec = f"Variant {best['id']} ({best['name']}) meets targets: {best['winRate']}% win rate, {best['tradesPerDay']} trades/day"
    else:
        # Fall back to best overall even if target not met
        with_trades = [v for v in variants.values() if v["totalTrades"] > 0]
        if with_trades:
            b = sorted(with_trades, key=lambda v: (v["winRate"], v["totalPnLR"]), reverse=True)[0]
            rec = f"No variant met both targets. Best overall: {b['id']} ({b['name']}) — {b['winRate']}% WR, {b['tradesPerDay']} trades/day"
            best = b

    return {"variants": variants, "best": best, "recommendation": rec}
