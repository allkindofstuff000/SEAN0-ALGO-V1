"""Signal sanity guard — a last line of defence before a live bot fires.

Every live bot (RSI EMA, VWAP+ST, BTC RSI EMA) computes entry / SL / TP, then
calls `check_signal(...)` right before sending to Telegram + Mongo. If anything
is off — bad SL/TP geometry, insane risk:reward, absurd distances, a bad-tick
entry far from the recent candle, or a non-finite/zero value — the signal is
REJECTED (skipped + logged) instead of firing a garbage alert.

This is defensive: a single corrupt price tick, a NaN indicator, or a logic slip
that flips SL/TP should never reach the user as a tradeable signal.
"""
from __future__ import annotations

import math
from typing import Optional

# Tunable bounds (deliberately wide — this catches *garbage*, not marginal setups).
MIN_RR = 0.3          # take_profit_dist / stop_loss_dist floor
MAX_RR = 6.0          # ... and ceiling
MAX_DIST_FRAC = 0.15  # SL/TP distance may not exceed 15% of entry price
MIN_SL_ATR_FRAC = 0.1  # SL distance must be at least 0.1×ATR (no ~zero stop)
MAX_REF_DEV_FRAC = 0.05  # entry may not deviate >5% from the reference price (bad tick)


def _finite(v) -> bool:
    try:
        return v is not None and math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def check_signal(
    *,
    direction: str,
    entry: float,
    stop_loss: float,
    take_profit: float,
    atr: float,
    ref_price: Optional[float] = None,
    recent_high: Optional[float] = None,
    recent_low: Optional[float] = None,
) -> tuple[bool, str]:
    """Return (ok, reason). ok=False means DO NOT fire; reason explains why."""
    # 1. finite + positive
    for name, v in (("entry", entry), ("stop_loss", stop_loss), ("take_profit", take_profit), ("atr", atr)):
        if not _finite(v):
            return False, f"non-finite {name}={v}"
    entry = float(entry); sl = float(stop_loss); tp = float(take_profit); atr = float(atr)
    if entry <= 0:
        return False, f"entry<=0 ({entry})"
    if atr <= 0:
        return False, f"atr<=0 ({atr})"

    d = str(direction).upper()
    # 2. geometry — SL/TP on the correct side of entry
    if d == "BUY":
        if not (sl < entry < tp):
            return False, f"BUY geometry violated (sl={sl:.4f} < entry={entry:.4f} < tp={tp:.4f})"
    elif d == "SELL":
        if not (tp < entry < sl):
            return False, f"SELL geometry violated (tp={tp:.4f} < entry={entry:.4f} < sl={sl:.4f})"
    else:
        return False, f"unknown direction '{direction}'"

    sl_dist = abs(entry - sl)
    tp_dist = abs(tp - entry)
    if sl_dist <= 0 or tp_dist <= 0:
        return False, f"zero SL/TP distance (sl_dist={sl_dist}, tp_dist={tp_dist})"

    # 3. risk:reward within sane bounds
    rr = tp_dist / sl_dist
    if not (MIN_RR <= rr <= MAX_RR):
        return False, f"RR out of bounds ({rr:.2f}, allowed {MIN_RR}-{MAX_RR})"

    # 4. distances not absurd relative to price / ATR
    if sl_dist > entry * MAX_DIST_FRAC or tp_dist > entry * MAX_DIST_FRAC:
        return False, f"SL/TP distance too large (sl={sl_dist:.4f} tp={tp_dist:.4f} vs {MAX_DIST_FRAC:.0%} of {entry:.2f})"
    if sl_dist < atr * MIN_SL_ATR_FRAC:
        return False, f"SL distance too small vs ATR ({sl_dist:.4f} < {atr * MIN_SL_ATR_FRAC:.4f})"

    # 5. entry not wildly off the reference (live) price — catches a bad tick
    if _finite(ref_price) and float(ref_price) > 0:
        dev = abs(entry - float(ref_price)) / float(ref_price)
        if dev > MAX_REF_DEV_FRAC:
            return False, f"entry {entry:.2f} deviates {dev * 100:.1f}% from ref {float(ref_price):.2f}"

    # 6. entry within a sane band around the signal candle's range
    if _finite(recent_high) and _finite(recent_low):
        rh = float(recent_high); rl = float(recent_low)
        if rh >= rl > 0:
            pad = (rh - rl) * 0.5 + atr  # allow half the bar range + 1 ATR beyond
            if entry > rh + pad or entry < rl - pad:
                return False, f"entry {entry:.2f} outside recent range [{rl:.2f}, {rh:.2f}] +/- pad"

    return True, "ok"
