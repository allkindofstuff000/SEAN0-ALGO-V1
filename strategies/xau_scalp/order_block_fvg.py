from __future__ import annotations

from typing import Any

from . import config as cfg
from .utils import candle_body, candle_range, is_bearish, is_bullish, normalize_candles


class OrderBlockFVG:
    def __init__(self, config=cfg, candleStore=None):
        self.config = config
        self.candle_store = candleStore

    def findOrderBlocks(self, m5Candles: list[dict[str, Any]], bias: str) -> list[dict[str, Any]]:
        candles = normalize_candles(m5Candles)[-max(self.config.CANDLE_LOOKBACK_M5, self.config.OB_LOOKBACK + 10):]
        blocks: list[dict[str, Any]] = []
        if len(candles) < 6:
            return blocks

        start = max(1, len(candles) - self.config.OB_LOOKBACK)
        for i in range(start, len(candles) - 3):
            ob_candle = candles[i]
            future = candles[i + 1:i + 4]
            if bias == "bullish":
                if not is_bearish(ob_candle):
                    continue
                directional_bars = sum(1 for c in future if is_bullish(c))
                impulse_move = ((max(c["high"] for c in future) - ob_candle["close"]) / ob_candle["close"]) * 100 if ob_candle["close"] else 0
            elif bias == "bearish":
                if not is_bullish(ob_candle):
                    continue
                directional_bars = sum(1 for c in future if is_bearish(c))
                impulse_move = ((ob_candle["close"] - min(c["low"] for c in future)) / ob_candle["close"]) * 100 if ob_candle["close"] else 0
            else:
                continue

            total_range = candle_range(ob_candle) or 1.0
            body_pct = candle_body(ob_candle) / total_range
            if directional_bars < 2 or impulse_move < self.config.OB_MIN_IMPULSE_PCT or body_pct < self.config.OB_VALID_CANDLE_BODY:
                continue

            zone = {
                "kind": "ob",
                "type": bias,
                "zoneHigh": float(ob_candle["high"]),
                "zoneLow": float(ob_candle["low"]),
                "midpoint": round((float(ob_candle["high"]) + float(ob_candle["low"])) / 2, 2),
                "formed": ob_candle["time"],
                "timesVisited": 0,
                "mitigated": False,
                "strength": self._order_block_strength(impulse_move, 0),
                "obCandle": dict(ob_candle),
            }
            future_after = candles[i + 4:]
            zone["timesVisited"] = self._count_zone_visits(zone, future_after)
            zone["strength"] = self._order_block_strength(impulse_move, zone["timesVisited"])
            zone = self.checkMitigation(zone, future_after)
            blocks.append(zone)
        return blocks

    def findFVGs(self, m5Candles: list[dict[str, Any]], bias: str) -> list[dict[str, Any]]:
        candles = normalize_candles(m5Candles)[-max(self.config.CANDLE_LOOKBACK_M5, self.config.FVG_MAX_AGE_CANDLES + 10):]
        gaps: list[dict[str, Any]] = []
        if len(candles) < 3:
            return gaps

        for i in range(1, len(candles) - 1):
            prev_candle = candles[i - 1]
            mid_candle = candles[i]
            next_candle = candles[i + 1]

            if bias == "bullish" and next_candle["low"] > prev_candle["high"] and is_bullish(mid_candle):
                fvg_low = float(prev_candle["high"])
                fvg_high = float(next_candle["low"])
            elif bias == "bearish" and next_candle["high"] < prev_candle["low"] and is_bearish(mid_candle):
                fvg_low = float(next_candle["high"])
                fvg_high = float(prev_candle["low"])
            else:
                continue

            size = fvg_high - fvg_low
            if size < self.config.FVG_MIN_SIZE_PRICE:
                continue

            zone = {
                "kind": "fvg",
                "type": bias,
                "fvgHigh": round(fvg_high, 2),
                "fvgLow": round(fvg_low, 2),
                "midpoint": round((fvg_high + fvg_low) / 2, 2),
                "size": round(size, 2),
                "formed": mid_candle["time"],
                "candles": [prev_candle["time"], mid_candle["time"], next_candle["time"]],
                "mitigated": False,
                "mitPct": 0.0,
                "timesVisited": 0,
            }
            future_after = candles[i + 2:]
            zone["timesVisited"] = self._count_gap_visits(zone, future_after)
            zone = self.checkMitigation(zone, future_after)
            gaps.append(zone)
        return gaps

    def checkMitigation(self, zone: dict[str, Any], recentCandles: list[dict[str, Any]]) -> dict[str, Any]:
        if zone.get("kind") == "ob":
            visits = 0
            for candle in recentCandles:
                if candle["high"] >= zone["zoneLow"] and candle["low"] <= zone["zoneHigh"]:
                    visits += 1
                if zone["type"] == "bullish" and candle["close"] < zone["zoneLow"]:
                    zone["mitigated"] = True
                if zone["type"] == "bearish" and candle["close"] > zone["zoneHigh"]:
                    zone["mitigated"] = True
            zone["timesVisited"] = max(zone.get("timesVisited", 0), visits)
            if zone["timesVisited"] > self.config.OB_MAX_TESTS:
                zone["mitigated"] = True
            return zone

        gap_low = zone["fvgLow"]
        gap_high = zone["fvgHigh"]
        gap_size = max(gap_high - gap_low, 0.0001)
        max_fill = 0.0
        visits = 0
        for candle in recentCandles:
            if zone["type"] == "bullish" and candle["low"] < gap_high:
                visits += 1
                fill_depth = gap_high - max(candle["low"], gap_low)
                max_fill = max(max_fill, fill_depth)
            if zone["type"] == "bearish" and candle["high"] > gap_low:
                visits += 1
                fill_depth = min(candle["high"], gap_high) - gap_low
                max_fill = max(max_fill, fill_depth)
        zone["timesVisited"] = max(zone.get("timesVisited", 0), visits)
        zone["mitPct"] = round(min(100.0, max_fill / gap_size * 100.0), 1)
        zone["mitigated"] = zone["mitPct"] >= self.config.FVG_MITIGATION_PCT * 100
        return zone

    def getBestZone(self, m5Candles: list[dict[str, Any]], currentPrice: float, bias: str) -> dict[str, Any] | None:
        zones = []
        zones.extend(self.findFVGs(m5Candles, bias))
        zones.extend(self.findOrderBlocks(m5Candles, bias))

        valid: list[tuple[int, int, float, dict[str, Any]]] = []
        for zone in zones:
            if zone.get("mitigated"):
                continue
            distance = self._distance_to_zone(currentPrice, zone)
            freshness_rank = 0 if zone.get("timesVisited", 0) == 0 else 1
            type_rank = 0 if zone.get("kind") == "fvg" else 1
            zone["distance"] = round(distance, 3)
            zone["fresh"] = zone.get("timesVisited", 0) == 0
            valid.append((freshness_rank, type_rank, distance, zone))

        if not valid:
            return None
        valid.sort(key=lambda item: (item[0], item[1], item[2]))
        return valid[0][3]

    def isPriceInZone(self, price: float, zone: dict[str, Any] | None) -> bool:
        if not zone:
            return False
        low, high = self._zone_bounds(zone)
        return (low - self.config.OB_BUFFER_PRICE) <= price <= (high + self.config.OB_BUFFER_PRICE)

    def getZoneDescription(self, zone: dict[str, Any] | None) -> str:
        if not zone:
            return "No valid zone"
        low, high = self._zone_bounds(zone)
        freshness = "Fresh" if zone.get("timesVisited", 0) == 0 else f"Tested {zone.get('timesVisited', 0)}x"
        kind = "OB" if zone.get("kind") == "ob" else "FVG"
        return f"{zone.get('type', 'neutral').capitalize()} {kind} @ {low:.2f}-{high:.2f} ({freshness})"

    def _distance_to_zone(self, price: float, zone: dict[str, Any]) -> float:
        low, high = self._zone_bounds(zone)
        if low <= price <= high:
            return 0.0
        return min(abs(price - low), abs(price - high))

    def _zone_bounds(self, zone: dict[str, Any]) -> tuple[float, float]:
        if zone.get("kind") == "ob":
            return float(zone["zoneLow"]), float(zone["zoneHigh"])
        return float(zone["fvgLow"]), float(zone["fvgHigh"])

    def _count_zone_visits(self, zone: dict[str, Any], candles: list[dict[str, Any]]) -> int:
        return sum(1 for candle in candles if candle["high"] >= zone["zoneLow"] and candle["low"] <= zone["zoneHigh"])

    def _count_gap_visits(self, zone: dict[str, Any], candles: list[dict[str, Any]]) -> int:
        return sum(1 for candle in candles if candle["high"] >= zone["fvgLow"] and candle["low"] <= zone["fvgHigh"])

    def _order_block_strength(self, impulse_move: float, visits: int) -> str:
        if impulse_move >= self.config.OB_MIN_IMPULSE_PCT * 2 and visits == 0:
            return "strong"
        if impulse_move >= self.config.OB_MIN_IMPULSE_PCT and visits <= 1:
            return "moderate"
        return "weak"
