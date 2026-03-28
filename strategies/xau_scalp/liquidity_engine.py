from __future__ import annotations

from typing import Any

from . import config as cfg
from .utils import normalize_candles, price_to_pips, to_utc_timestamp


class LiquidityEngine:
    def __init__(self, config=cfg, candleStore=None):
        self.config = config
        self.candle_store = candleStore

    def findSSLPools(self, m1Candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
        candles = normalize_candles(m1Candles)[-max(self.config.CANDLE_LOOKBACK_M1, self.config.SSL_LOOKBACK + 20):]
        return self._find_pools(candles, side="low")

    def findBSLPools(self, m1Candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
        candles = normalize_candles(m1Candles)[-max(self.config.CANDLE_LOOKBACK_M1, self.config.BSL_LOOKBACK + 20):]
        return self._find_pools(candles, side="high")

    def detectSSLSweep(self, m1Candles: list[dict[str, Any]], sslPool: dict[str, Any]) -> dict[str, Any]:
        candles = normalize_candles(m1Candles)
        return self._detect_sweep(candles, sslPool, direction="bullish")

    def detectBSLSweep(self, m1Candles: list[dict[str, Any]], bslPool: dict[str, Any]) -> dict[str, Any]:
        candles = normalize_candles(m1Candles)
        return self._detect_sweep(candles, bslPool, direction="bearish")

    def getSweepQuality(self, sweep: dict[str, Any], m1Candles: list[dict[str, Any]]) -> str:
        if not sweep.get("swept"):
            return "low"
        depth = sweep.get("sweepDepth", 0.0)
        speed = sweep.get("recoverySpeed", "slow")
        if 2 <= depth <= 8 and speed in {"instant", "fast"}:
            return "high"
        if speed in {"instant", "fast", "slow"}:
            return "medium"
        return "low"

    def getNearestBSL(self, m1Candles: list[dict[str, Any]], currentPrice: float) -> dict[str, Any] | None:
        pools = [pool for pool in self.findBSLPools(m1Candles) if pool["price"] > currentPrice]
        if not pools:
            return None
        return sorted(pools, key=lambda pool: pool["price"])[0]

    def getNearestSSL(self, m1Candles: list[dict[str, Any]], currentPrice: float) -> dict[str, Any] | None:
        pools = [pool for pool in self.findSSLPools(m1Candles) if pool["price"] < currentPrice]
        if not pools:
            return None
        return sorted(pools, key=lambda pool: pool["price"], reverse=True)[0]

    def _find_pools(self, candles: list[dict[str, Any]], *, side: str) -> list[dict[str, Any]]:
        if len(candles) < 5:
            return []

        tolerance = self.config.EQUAL_LEVEL_TOLERANCE
        lookback = max(self.config.SSL_LOOKBACK, self.config.BSL_LOOKBACK)
        pools: list[dict[str, Any]] = []

        for idx, candle in enumerate(candles[-lookback:]):
            price = float(candle["low"] if side == "low" else candle["high"])
            existing = next((pool for pool in pools if abs(pool["price"] - price) <= tolerance and pool["type"].startswith("equal_")), None)
            if existing:
                existing["strength"] += 1
                existing["formed"] = candle["time"]
                existing["price"] = round((existing["price"] + price) / 2, 2)
            else:
                pools.append(
                    {
                        "type": "equal_lows" if side == "low" else "equal_highs",
                        "price": round(price, 2),
                        "strength": 1,
                        "formed": candle["time"],
                        "swept": False,
                        "index": len(candles) - lookback + idx,
                    }
                )

        for i in range(2, len(candles) - 2):
            level = float(candles[i]["low"] if side == "low" else candles[i]["high"])
            left = candles[i - 2:i]
            right = candles[i + 1:i + 3]
            if side == "low":
                if all(level < c["low"] for c in left + right):
                    pools.append({"type": "swing_low", "price": round(level, 2), "strength": 1, "formed": candles[i]["time"], "swept": False, "index": i})
            else:
                if all(level > c["high"] for c in left + right):
                    pools.append({"type": "swing_high", "price": round(level, 2), "strength": 1, "formed": candles[i]["time"], "swept": False, "index": i})

        pools.extend(self._session_pool_levels(candles, side))

        filtered: list[dict[str, Any]] = []
        for pool in pools:
            if pool["type"].startswith("equal_") and pool["strength"] < self.config.MIN_LIQUIDITY_TESTS:
                continue
            filtered.append(pool)
        filtered.sort(key=lambda item: (-item["strength"], item["formed"]))
        return filtered

    def _detect_sweep(self, candles: list[dict[str, Any]], pool: dict[str, Any], *, direction: str) -> dict[str, Any]:
        if not candles:
            return {"swept": False, "validity": "no_data"}
        level = pool["price"]
        start_index = max(0, pool.get("index", len(candles) - 10))

        for i in range(start_index, len(candles)):
            candle = candles[i]
            if direction == "bullish":
                pierced = candle["low"] < level
                recovered = candle["close"] > level
                depth = price_to_pips(level - candle["low"]) if pierced else 0.0
            else:
                pierced = candle["high"] > level
                recovered = candle["close"] < level
                depth = price_to_pips(candle["high"] - level) if pierced else 0.0

            if not pierced:
                continue

            recovery_bars = 0
            if recovered:
                recovery_speed = "instant"
            else:
                recovery_speed = "slow"
                for j in range(i + 1, min(i + 3, len(candles))):
                    nxt = candles[j]
                    recovery_bars += 1
                    if direction == "bullish" and nxt["close"] > level:
                        recovered = True
                        recovery_speed = "fast" if recovery_bars == 1 else "slow"
                        break
                    if direction == "bearish" and nxt["close"] < level:
                        recovered = True
                        recovery_speed = "fast" if recovery_bars == 1 else "slow"
                        break

            validity = "valid"
            if depth < self.config.SWEEP_MIN_PIPS:
                validity = "too_shallow"
            elif depth > self.config.SWEEP_MAX_PIPS:
                validity = "too_deep"

            result = {
                "swept": pierced and recovered and validity == "valid",
                "sweepCandle": candle,
                "sweepDepth": round(depth, 1),
                "recoverySpeed": recovery_speed,
                "validity": validity,
                "level": level,
                "candleIndex": i,
                "pool": pool,
            }
            result["quality"] = self.getSweepQuality(result, candles)
            return result

        return {"swept": False, "validity": "not_found", "pool": pool}

    def _session_pool_levels(self, candles: list[dict[str, Any]], side: str) -> list[dict[str, Any]]:
        if len(candles) < 30:
            return []
        rows = []
        for idx, candle in enumerate(candles):
            ts = to_utc_timestamp(candle["time"])
            rows.append({"date": ts.date(), "idx": idx, "candle": candle})
        dates = sorted({row["date"] for row in rows})
        if len(dates) < 2:
            return []
        prev_date = dates[-2]
        prev_session = [row for row in rows if row["date"] == prev_date]
        if not prev_session:
            return []
        if side == "low":
            row = min(prev_session, key=lambda item: item["candle"]["low"])
            price = row["candle"]["low"]
            pool_type = "session_low"
        else:
            row = max(prev_session, key=lambda item: item["candle"]["high"])
            price = row["candle"]["high"]
            pool_type = "session_high"
        return [{"type": pool_type, "price": round(float(price), 2), "strength": 3, "formed": row["candle"]["time"], "swept": False, "index": row["idx"]}]
