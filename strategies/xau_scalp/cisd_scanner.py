from __future__ import annotations

from . import config as cfg
from .utils import normalize_candles


class CISDScanner:
    def __init__(self, config=cfg):
        self.config = config

    def detectCISD(self, m1Candles: list[dict], sweep: dict, bias: str) -> dict:
        candles = normalize_candles(m1Candles)
        if not candles or not sweep.get("swept"):
            return {"detected": False, "reason": "no_sweep"}

        sweep_index = sweep.get("candleIndex", len(candles) - 1)
        if bias == "bullish":
            micro_level = self.findLastMicroHigh(candles, sweep_index, self.config.CISD_LOOKBACK)
            if not micro_level:
                return {"detected": False, "reason": "no_micro_high"}
            for i in range(sweep_index + 1, min(len(candles), sweep_index + 1 + 5)):
                if candles[i]["close"] > micro_level["price"]:
                    return {
                        "detected": True,
                        "cisdCandle": candles[i],
                        "flippedLevel": micro_level["price"],
                        "prevBias": "bearish",
                        "newBias": "bullish",
                        "barsAfterSweep": i - sweep_index,
                        "candleIndex": i,
                    }
        else:
            micro_level = self.findLastMicroLow(candles, sweep_index, self.config.CISD_LOOKBACK)
            if not micro_level:
                return {"detected": False, "reason": "no_micro_low"}
            for i in range(sweep_index + 1, min(len(candles), sweep_index + 1 + 5)):
                if candles[i]["close"] < micro_level["price"]:
                    return {
                        "detected": True,
                        "cisdCandle": candles[i],
                        "flippedLevel": micro_level["price"],
                        "prevBias": "bullish",
                        "newBias": "bearish",
                        "barsAfterSweep": i - sweep_index,
                        "candleIndex": i,
                    }
        return {"detected": False, "reason": "not_confirmed"}

    def findLastMicroHigh(self, m1Candles: list[dict], sweepIndex: int, lookback: int) -> dict | None:
        start = max(0, sweepIndex - lookback)
        window = m1Candles[start:sweepIndex]
        if not window:
            return None
        highest = max(window, key=lambda candle: candle["high"])
        return {"price": highest["high"], "index": m1Candles.index(highest)}

    def findLastMicroLow(self, m1Candles: list[dict], sweepIndex: int, lookback: int) -> dict | None:
        start = max(0, sweepIndex - lookback)
        window = m1Candles[start:sweepIndex]
        if not window:
            return None
        lowest = min(window, key=lambda candle: candle["low"])
        return {"price": lowest["low"], "index": m1Candles.index(lowest)}

    def isCISDValid(self, cisd: dict) -> bool:
        bars_after = int(cisd.get("barsAfterSweep", 99))
        return cisd.get("detected", False) and 1 <= bars_after <= 5
