from __future__ import annotations

from . import config as cfg
from .utils import candle_body, candle_range, is_bearish, is_bullish, normalize_candles


class DisplacementScanner:
    def __init__(self, config=cfg):
        self.config = config

    def findDisplacement(self, m1Candles: list[dict], bias: str, start_index: int = 0) -> dict:
        candles = normalize_candles(m1Candles)
        if len(candles) < 22:
            return {"found": False, "reasons": ["not_enough_candles"]}

        found = None
        for i in range(max(1, start_index), len(candles) - 1):
            avg_range = self.getAverageRange(candles[max(0, i - 20):i], 20)
            check = self.isDisplacementCandle(candles[i], avg_range, bias)
            if not check["valid"]:
                continue
            fvg = self.extractDisplacementFVG(candles, i, bias)
            if self.config.DISPLACEMENT_FVG_REQ and fvg is None:
                continue
            found = {
                "found": True,
                "candle": candles[i],
                "bodyPct": round(check["bodyPct"], 3),
                "closePct": round(check["closePct"], 3),
                "rangeVsAvg": round(check["rangeVsAvg"], 3),
                "createdFVG": fvg is not None,
                "fvg": fvg,
                "candleIndex": i,
            }
        return found or {"found": False, "reasons": ["no_displacement"]}

    def extractDisplacementFVG(self, candles: list[dict], displacementIndex: int, bias: str) -> dict | None:
        if displacementIndex <= 0 or displacementIndex >= len(candles) - 1:
            return None

        prev_candle = candles[displacementIndex - 1]
        next_candle = candles[displacementIndex + 1]
        if bias == "bullish":
            fvg_low = float(prev_candle["high"])
            fvg_high = float(next_candle["low"])
        else:
            fvg_low = float(next_candle["high"])
            fvg_high = float(prev_candle["low"])

        if fvg_high <= fvg_low:
            return None
        size = fvg_high - fvg_low
        if size < self.config.FVG_MIN_SIZE_PRICE:
            return None
        return {
            "type": bias,
            "fvgHigh": round(fvg_high, 2),
            "fvgLow": round(fvg_low, 2),
            "fvgMid": round((fvg_high + fvg_low) / 2, 2),
            "size": round(size, 2),
            "formed": candles[displacementIndex]["time"],
        }

    def getAverageRange(self, candles: list[dict], lookback: int) -> float:
        if not candles:
            return 0.0
        sample = candles[-lookback:]
        ranges = [candle_range(candle) for candle in sample]
        return sum(ranges) / max(len(ranges), 1)

    def isDisplacementCandle(self, candle: dict, avgRange: float, bias: str | None = None) -> dict:
        reasons: list[str] = []
        rng = candle_range(candle)
        body = candle_body(candle)
        body_pct = body / rng if rng else 0.0
        if rng <= 0:
            reasons.append("zero_range")

        if bias == "bullish":
            close_pct = (candle["close"] - candle["low"]) / rng if rng else 0.0
        else:
            close_pct = (candle["high"] - candle["close"]) / rng if rng else 0.0

        if bias == "bullish" and not is_bullish(candle):
            reasons.append("not_bullish")
        if bias == "bearish" and not is_bearish(candle):
            reasons.append("not_bearish")
        if body_pct < self.config.DISPLACEMENT_BODY_PCT:
            reasons.append("body_too_small")
        if close_pct < self.config.DISPLACEMENT_CLOSE_PCT:
            reasons.append("weak_close")
        if rng < self.config.DISPLACEMENT_MIN_SIZE:
            reasons.append("range_too_small")
        if avgRange and rng < avgRange * 1.4:
            reasons.append("not_large_vs_avg")

        return {
            "valid": not reasons,
            "reasons": reasons,
            "bodyPct": body_pct,
            "closePct": close_pct,
            "rangeVsAvg": rng / avgRange if avgRange else 0.0,
        }
