from __future__ import annotations

from typing import Any

import pandas as pd

from . import config as cfg
from .utils import normalize_candles, to_utc_timestamp


class SessionFilter:
    def __init__(self, config=cfg):
        self.config = config

    def getCurrentSession(self, timestamp: Any) -> str:
        hour = to_utc_timestamp(timestamp).hour
        if 0 <= hour < self.config.LONDON_OPEN_UTC:
            return "asian"
        if self.config.LONDON_OPEN_UTC <= hour < self.config.NY_OPEN_UTC:
            return "london"
        if self.config.NY_OPEN_UTC <= hour < self.config.LONDON_CLOSE_UTC:
            return "overlap"
        if self.config.LONDON_CLOSE_UTC <= hour < self.config.NY_CLOSE_UTC:
            return "newyork"
        return "off"

    def isKillZone(self, timestamp: Any) -> dict[str, Any]:
        hour = to_utc_timestamp(timestamp).hour
        if 8 <= hour < 10:
            return {"inKillZone": True, "zoneName": "London Kill Zone", "priority": "HIGH"}
        if 13 <= hour < 15:
            return {"inKillZone": True, "zoneName": "NY Kill Zone", "priority": "HIGH"}
        if 17 <= hour < 19:
            return {"inKillZone": False, "zoneName": "NY Lunch", "priority": "LOW"}
        if 20 <= hour < 21:
            return {"inKillZone": False, "zoneName": "NY Close", "priority": "NONE"}
        return {"inKillZone": False, "zoneName": None, "priority": self.getSessionPriority(timestamp)}

    def canTrade(self, timestamp: Any) -> dict[str, Any]:
        session = self.getCurrentSession(timestamp)
        kz = self.isKillZone(timestamp)
        if session == "asian":
            return {"allowed": False, "reason": "Asian Session — Waiting for London Open", "session": session, "priority": "NONE", "zoneName": "Asian Session"}
        if session == "off":
            return {"allowed": False, "reason": "Off Hours — Waiting for London Open", "session": session, "priority": "NONE", "zoneName": "Off Hours"}
        if kz["zoneName"] == "NY Lunch":
            return {"allowed": False, "reason": "NY Lunch — Low probability, skipping", "session": session, "priority": "LOW", "zoneName": kz["zoneName"]}
        if kz["zoneName"] == "NY Close":
            return {"allowed": False, "reason": "NY Close — Avoiding late-session conditions", "session": session, "priority": "NONE", "zoneName": kz["zoneName"]}

        if kz["inKillZone"]:
            return {"allowed": True, "reason": f"{kz['zoneName']} — HIGH PRIORITY", "session": session, "priority": "HIGH", "zoneName": kz["zoneName"]}
        if session == "london" and self.config.TRADE_LONDON_SESSION:
            return {"allowed": True, "reason": "London Session — Active", "session": session, "priority": "MEDIUM", "zoneName": "London Session"}
        if session in {"newyork", "overlap"} and self.config.TRADE_NEWYORK_SESSION:
            zone = "London / NY Overlap" if session == "overlap" else "New York Session"
            priority = "HIGH" if session == "overlap" and self.config.OVERLAP_BONUS else "MEDIUM"
            return {"allowed": True, "reason": f"{zone} — {priority} PRIORITY", "session": session, "priority": priority, "zoneName": zone}
        return {"allowed": False, "reason": "Session disabled in config", "session": session, "priority": "NONE", "zoneName": "Disabled"}

    def getSessionPriority(self, timestamp: Any) -> str:
        session = self.getCurrentSession(timestamp)
        if session == "overlap":
            return "HIGH" if self.config.OVERLAP_BONUS else "MEDIUM"
        if session in {"london", "newyork"}:
            return "MEDIUM"
        return "NONE"

    def getNextHighPrioritySession(self, currentTime: Any) -> dict[str, Any]:
        ts = to_utc_timestamp(currentTime)
        candidates = []
        for name, hour in (("London Kill Zone", 8), ("NY Kill Zone", 13)):
            candidate = ts.normalize() + pd.Timedelta(hours=hour)
            if candidate <= ts:
                candidate += pd.Timedelta(days=1)
            candidates.append((name, candidate))
        next_name, next_ts = min(candidates, key=lambda item: item[1])
        mins = max(0, int((next_ts - ts).total_seconds() // 60))
        return {"sessionName": next_name, "startsIn_minutes": mins}

    def isPDA(self, m1Candles: list[dict]) -> dict[str, Any]:
        candles = normalize_candles(m1Candles)
        if len(candles) < 5:
            return {"zone": "equilibrium", "pct": 50.0}
        window = candles[-20:]
        range_high = max(candle["high"] for candle in window)
        range_low = min(candle["low"] for candle in window)
        close = window[-1]["close"]
        if range_high == range_low:
            return {"zone": "equilibrium", "pct": 50.0}
        pct = ((close - range_low) / (range_high - range_low)) * 100
        if pct >= 55:
            zone = "premium"
        elif pct <= 45:
            zone = "discount"
        else:
            zone = "equilibrium"
        return {"zone": zone, "pct": round(pct, 1)}
