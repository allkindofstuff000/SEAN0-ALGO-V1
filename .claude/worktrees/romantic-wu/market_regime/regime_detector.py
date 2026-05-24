from __future__ import annotations

from typing import Any

import pandas as pd


TREND_ATR_MULTIPLIER = 1.5
RANGE_ATR_MULTIPLIER = 0.5
HIGH_VOLATILITY_MULTIPLIER = 1.3
LOW_VOLATILITY_MULTIPLIER = 0.8
EPSILON = 1e-9


def build_regime_input_frame(
    trend_candles: pd.DataFrame,
    entry_candles: pd.DataFrame,
) -> pd.DataFrame:
    """
    Build a regime-ready snapshot by combining 15m trend EMA values with 5m ATR/RSI values.

    The detector only needs the latest aligned state, so a single-row DataFrame keeps the
    integration simple for both live evaluation and OpenClaw tools.
    """

    if trend_candles is None or trend_candles.empty:
        raise ValueError("Trend candles are required to build regime input.")
    if entry_candles is None or entry_candles.empty:
        raise ValueError("Entry candles are required to build regime input.")

    trend_last = trend_candles.iloc[-1]
    entry_last = entry_candles.iloc[-1]
    timestamp = entry_last.get("timestamp", trend_last.get("timestamp"))

    return pd.DataFrame(
        [
            {
                "timestamp": timestamp,
                "ema50": float(trend_last["ema50"]),
                "ema200": float(trend_last["ema200"]),
                "atr14": float(entry_last["atr14"]),
                "atr20_avg": float(entry_last.get("atr20_avg", 0.0) or 0.0),
                "rsi14": float(entry_last.get("rsi14", 50.0) or 50.0),
            }
        ]
    )


def detect_market_regime(df: pd.DataFrame) -> dict[str, Any]:
    """
    Detect the active market regime.

    Returns a primary regime plus explicit trend/volatility sub-regimes so the live strategy
    can adapt without losing the orthogonal volatility context.
    """

    if df is None or df.empty:
        raise ValueError("Indicator frame is required for regime detection.")

    row = df.iloc[-1]
    required_columns = {"ema50", "ema200", "atr14", "rsi14"}
    missing = required_columns - set(df.columns)
    if missing:
        raise ValueError(f"Missing required regime columns: {sorted(missing)}")

    atr_avg_column = "atr20_avg" if "atr20_avg" in df.columns else "atr_avg"
    if atr_avg_column not in df.columns:
        raise ValueError("Indicator frame must include atr20_avg or atr_avg.")

    ema50 = _safe_float(row["ema50"])
    ema200 = _safe_float(row["ema200"])
    atr14 = max(_safe_float(row["atr14"]), 0.0)
    atr_avg = max(_safe_float(row[atr_avg_column]), 0.0)
    rsi14 = _safe_float(row["rsi14"])
    ema_gap = abs(ema50 - ema200)

    trend_threshold = atr14 * TREND_ATR_MULTIPLIER
    range_threshold = atr14 * RANGE_ATR_MULTIPLIER
    high_volatility_threshold = atr_avg * HIGH_VOLATILITY_MULTIPLIER
    low_volatility_threshold = atr_avg * LOW_VOLATILITY_MULTIPLIER

    trend_regime = "neutral"
    range_condition = False
    trend_condition = False
    if atr14 > 0:
        trend_condition = ema_gap > trend_threshold
        range_condition = ema_gap < range_threshold
        if trend_condition:
            trend_regime = "trend"
        elif range_condition:
            trend_regime = "range"

    volatility_regime = "normal_volatility"
    high_volatility_condition = False
    low_volatility_condition = False
    if atr_avg > 0:
        high_volatility_condition = atr14 > high_volatility_threshold
        low_volatility_condition = atr14 < low_volatility_threshold
        if high_volatility_condition:
            volatility_regime = "high_volatility"
        elif low_volatility_condition:
            volatility_regime = "low_volatility"

    confidence_map = {
        "trend": _positive_condition_confidence(ema_gap, trend_threshold),
        "range": _inverse_condition_confidence(ema_gap, range_threshold),
        "high_volatility": _positive_condition_confidence(atr14, high_volatility_threshold),
        "low_volatility": _inverse_condition_confidence(atr14, low_volatility_threshold),
    }

    if low_volatility_condition:
        regime = "low_volatility"
        confidence = confidence_map[regime]
    elif high_volatility_condition:
        regime = "high_volatility"
        confidence = confidence_map[regime]
    elif trend_condition:
        regime = "trend"
        confidence = confidence_map[regime]
    elif range_condition:
        regime = "range"
        confidence = confidence_map[regime]
    else:
        if trend_regime == "neutral":
            regime = "range" if ema_gap <= max(atr14, EPSILON) else "trend"
        else:
            regime = trend_regime
        confidence = _fallback_confidence(ema_gap=ema_gap, atr14=atr14, atr_avg=atr_avg)

    timestamp = row.get("timestamp")
    return {
        "timestamp": None if timestamp is None else pd.Timestamp(timestamp).isoformat(),
        "regime": regime,
        "confidence": round(float(confidence), 4),
        "trend_regime": trend_regime,
        "volatility_regime": volatility_regime,
        "ema_gap": round(float(ema_gap), 6),
        "atr14": round(float(atr14), 6),
        "atr_avg": round(float(atr_avg), 6),
        "rsi14": round(float(rsi14), 4),
        "trend_ratio": round(_safe_ratio(ema_gap, trend_threshold), 4),
        "range_ratio": round(_safe_ratio(range_threshold, max(ema_gap, EPSILON)), 4),
        "atr_ratio": round(_safe_ratio(atr14, atr_avg), 4),
    }


def _positive_condition_confidence(measured: float, threshold: float) -> float:
    ratio = _safe_ratio(measured, threshold)
    if ratio < 1.0:
        return max(0.15, min(0.49, ratio * 0.45))
    return min(0.99, 0.6 + min(1.25, ratio - 1.0) * 0.28)


def _inverse_condition_confidence(measured: float, threshold: float) -> float:
    if threshold <= 0:
        return 0.25
    ratio = measured / threshold
    if ratio <= 1.0:
        return min(0.99, 0.6 + min(1.0, 1.0 - ratio) * 0.35)
    closeness = max(0.0, 1.0 / max(ratio, 1.0))
    return max(0.15, min(0.49, closeness * 0.45))


def _fallback_confidence(*, ema_gap: float, atr14: float, atr_avg: float) -> float:
    if atr14 <= 0 or atr_avg <= 0:
        return 0.35
    directional_signal = _safe_ratio(ema_gap, atr14)
    volatility_signal = abs(_safe_ratio(atr14, atr_avg) - 1.0)
    return round(min(0.55, 0.28 + min(0.2, directional_signal * 0.08) + min(0.12, volatility_signal * 0.12)), 4)


def _safe_ratio(numerator: float, denominator: float) -> float:
    return float(numerator) / max(float(denominator), EPSILON)


def _safe_float(value: Any) -> float:
    return float(value if value is not None else 0.0)
