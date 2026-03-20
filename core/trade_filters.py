from __future__ import annotations

from typing import Any

import pandas as pd


def check_trend_strength(ema50: float, ema200: float, atr: float) -> dict[str, Any]:
    ema_distance = abs(float(ema50) - float(ema200))
    atr_value = float(atr)
    if atr_value <= 0:
        return {"allowed": True, "reason": "", "ema_distance": ema_distance, "atr": atr_value}

    if ema_distance < atr_value:
        return {
            "allowed": False,
            "reason": "Weak trend",
            "ema_distance": ema_distance,
            "atr": atr_value,
        }
    return {"allowed": True, "reason": "", "ema_distance": ema_distance, "atr": atr_value}


def check_no_trade_zone(candles: pd.DataFrame, atr: pd.Series) -> dict[str, Any]:
    recent_candles = candles.tail(20)
    recent_atr = atr.tail(20)
    if len(recent_candles) < 20 or len(recent_atr) < 20:
        return {"allowed": True, "reason": ""}

    range_high = float(recent_candles["high"].max())
    range_low = float(recent_candles["low"].min())
    range_size = range_high - range_low
    atr_mean = float(recent_atr.mean())

    if range_size < atr_mean * 2:
        return {"allowed": False, "reason": "No-Trade Zone: Sideways market"}
    return {"allowed": True, "reason": ""}


def check_low_volatility(atr: pd.Series) -> dict[str, Any]:
    recent_atr = atr.tail(20)
    if len(recent_atr) < 20:
        return {"allowed": True, "reason": ""}

    current_atr = float(recent_atr.iloc[-1])
    atr_avg = float(recent_atr.mean())
    if current_atr < atr_avg:
        return {"allowed": False, "reason": "Low volatility"}
    return {"allowed": True, "reason": ""}


def check_overextended_candle(candle: pd.Series, atr: float) -> dict[str, Any]:
    candle_size = abs(float(candle["close"]) - float(candle["open"]))
    if candle_size > atr * 1.5:
        return {"allowed": False, "reason": "Overextended candle"}
    return {"allowed": True, "reason": ""}


def run_trade_filters(
    candles: pd.DataFrame,
    *,
    trend_ema50: float | None = None,
    trend_ema200: float | None = None,
    trend_atr: float | None = None,
) -> dict[str, Any]:
    if candles is None or candles.empty or "atr14" not in candles.columns:
        return {"allowed": True, "reason": ""}

    atr_series = candles["atr14"]
    checks: tuple[dict[str, Any], ...]
    trend_check: tuple[dict[str, Any], ...] = ()
    if trend_ema50 is not None and trend_ema200 is not None and trend_atr is not None:
        trend_check = (check_trend_strength(trend_ema50, trend_ema200, trend_atr),)

    checks = trend_check + (
        check_no_trade_zone(candles, atr_series),
        check_low_volatility(atr_series),
        check_overextended_candle(candles.iloc[-1], float(atr_series.iloc[-1])),
    )
    for check in checks:
        if not check["allowed"]:
            return check
    return {"allowed": True, "reason": ""}
