from __future__ import annotations

from typing import Any

from . import config as cfg
from .utils import normalize_candles


class StructureAnalyzer:
    def __init__(self, config=cfg, candleStore=None):
        self.config = config
        self.candle_store = candleStore

    def analyzeBias(self, m15Candles: list[dict[str, Any]]) -> dict[str, Any]:
        candles = normalize_candles(m15Candles)[-max(self.config.CANDLE_LOOKBACK_M15, self.config.STRUCTURE_LOOKBACK + 10):]
        if len(candles) < self.config.SWING_PIVOT_BARS * 2 + 3:
            return {
                "bias": "neutral",
                "lastBOS": None,
                "lastCHoCH": None,
                "swingHighs": [],
                "swingLows": [],
                "strength": "weak",
            }

        swing_highs = self.findSwingHighs(candles, self.config.SWING_PIVOT_BARS)
        swing_lows = self.findSwingLows(candles, self.config.SWING_PIVOT_BARS)
        last_bos = self.detectBOS(swing_highs, swing_lows)
        bias = last_bos["direction"] if last_bos else "neutral"
        last_choch = self.detectCHoCH(swing_highs, swing_lows, bias, candles[-1]["close"])
        if last_choch and (last_bos is None or last_choch["timestamp"] >= last_bos["timestamp"]):
            bias = "neutral"

        return {
            "bias": bias,
            "lastBOS": last_bos,
            "lastCHoCH": last_choch,
            "swingHighs": swing_highs[-6:],
            "swingLows": swing_lows[-6:],
            "strength": self.getStructureStrength({"highs": swing_highs, "lows": swing_lows, "bos": last_bos}),
        }

    def findSwingHighs(self, candles: list[dict[str, Any]], pivotBars: int) -> list[dict[str, Any]]:
        swings: list[dict[str, Any]] = []
        for i in range(pivotBars, len(candles) - pivotBars):
            high = candles[i]["high"]
            left = candles[i - pivotBars:i]
            right = candles[i + 1:i + pivotBars + 1]
            if all(high > c["high"] for c in left) and all(high > c["high"] for c in right):
                swings.append(
                    {
                        "price": high,
                        "index": i,
                        "timestamp": candles[i]["time"],
                        "close": candles[i]["close"],
                    }
                )
        return swings

    def findSwingLows(self, candles: list[dict[str, Any]], pivotBars: int) -> list[dict[str, Any]]:
        swings: list[dict[str, Any]] = []
        for i in range(pivotBars, len(candles) - pivotBars):
            low = candles[i]["low"]
            left = candles[i - pivotBars:i]
            right = candles[i + 1:i + pivotBars + 1]
            if all(low < c["low"] for c in left) and all(low < c["low"] for c in right):
                swings.append(
                    {
                        "price": low,
                        "index": i,
                        "timestamp": candles[i]["time"],
                        "close": candles[i]["close"],
                    }
                )
        return swings

    def detectBOS(self, swingHighs: list[dict[str, Any]], swingLows: list[dict[str, Any]]) -> dict[str, Any] | None:
        candidates: list[dict[str, Any]] = []

        for prev, cur in zip(swingHighs[:-1], swingHighs[1:]):
            move_pct = ((cur["price"] - prev["price"]) / prev["price"]) * 100 if prev["price"] else 0
            if cur["price"] > prev["price"] and cur["close"] > prev["price"] and move_pct >= self.config.MIN_STRUCTURE_MOVE_PCT:
                candidates.append(
                    {
                        "direction": "bullish",
                        "price": cur["price"],
                        "timestamp": cur["timestamp"],
                        "movePct": round(move_pct, 3),
                    }
                )

        for prev, cur in zip(swingLows[:-1], swingLows[1:]):
            move_pct = ((prev["price"] - cur["price"]) / prev["price"]) * 100 if prev["price"] else 0
            if cur["price"] < prev["price"] and cur["close"] < prev["price"] and move_pct >= self.config.MIN_STRUCTURE_MOVE_PCT:
                candidates.append(
                    {
                        "direction": "bearish",
                        "price": cur["price"],
                        "timestamp": cur["timestamp"],
                        "movePct": round(move_pct, 3),
                    }
                )

        if not candidates:
            return None
        return sorted(candidates, key=lambda item: item["timestamp"])[-1]

    def detectCHoCH(
        self,
        swingHighs: list[dict[str, Any]],
        swingLows: list[dict[str, Any]],
        currentBias: str,
        latest_close: float | None = None,
    ) -> dict[str, Any] | None:
        if latest_close is None:
            return None
        if currentBias == "bullish" and swingLows:
            last_low = swingLows[-1]
            if latest_close < last_low["price"]:
                return {
                    "detected": True,
                    "direction": "bearish",
                    "price": last_low["price"],
                    "timestamp": last_low["timestamp"],
                }
        if currentBias == "bearish" and swingHighs:
            last_high = swingHighs[-1]
            if latest_close > last_high["price"]:
                return {
                    "detected": True,
                    "direction": "bullish",
                    "price": last_high["price"],
                    "timestamp": last_high["timestamp"],
                }
        return None

    def getCurrentBias(self, m15Candles: list[dict[str, Any]]) -> str:
        return self.analyzeBias(m15Candles)["bias"]

    def getStructureStrength(self, swingPoints: dict[str, Any]) -> str:
        highs = swingPoints.get("highs", [])
        lows = swingPoints.get("lows", [])
        bullish_sequences = self._count_sequences(highs, lows, bullish=True)
        bearish_sequences = self._count_sequences(highs, lows, bullish=False)
        count = max(bullish_sequences, bearish_sequences)
        if count >= 4:
            return "strong"
        if count >= 3:
            return "moderate"
        return "weak"

    def _count_sequences(self, highs: list[dict[str, Any]], lows: list[dict[str, Any]], *, bullish: bool) -> int:
        if len(highs) < 2 or len(lows) < 2:
            return 0

        high_count = 1
        for prev, cur in zip(highs[:-1], highs[1:]):
            if (cur["price"] > prev["price"]) if bullish else (cur["price"] < prev["price"]):
                high_count += 1
            else:
                high_count = 1

        low_count = 1
        for prev, cur in zip(lows[:-1], lows[1:]):
            if (cur["price"] > prev["price"]) if bullish else (cur["price"] < prev["price"]):
                low_count += 1
            else:
                low_count = 1

        return max(0, min(high_count, low_count) - 1)
