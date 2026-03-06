from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Any

import pandas as pd


@dataclass
class SessionEngine:
    """
    Detect the dominant institutional trading session in UTC.

    Session windows:
    - ASIAN: 22:00-06:59 UTC
    - LONDON: 07:00-11:59 UTC
    - OVERLAP: 12:00-15:59 UTC
    - NEW_YORK: 16:00-21:59 UTC
    """

    asian_start: time = time(22, 0)
    london_start: time = time(7, 0)
    overlap_start: time = time(12, 0)
    new_york_start: time = time(16, 0)
    asian_strength: float = 0.4
    london_strength: float = 0.8
    overlap_strength: float = 1.0
    new_york_strength: float = 0.8

    def detect(self, timestamp_utc: datetime | pd.Timestamp | None = None) -> dict[str, Any]:
        moment = self._coerce_timestamp(timestamp_utc)
        current_time = moment.time()

        if self._is_asian(current_time):
            session = "ASIAN"
            strength = self.asian_strength
        elif current_time >= self.new_york_start:
            session = "NEW_YORK"
            strength = self.new_york_strength
        elif current_time >= self.overlap_start:
            session = "OVERLAP"
            strength = self.overlap_strength
        elif current_time >= self.london_start:
            session = "LONDON"
            strength = self.london_strength
        else:
            session = "ASIAN"
            strength = self.asian_strength

        return {
            "session": session,
            "strength": strength,
            "timestamp_utc": moment.isoformat(),
        }

    def _is_asian(self, current_time: time) -> bool:
        return current_time >= self.asian_start or current_time < self.london_start

    @staticmethod
    def _coerce_timestamp(timestamp_utc: datetime | pd.Timestamp | None) -> datetime:
        if timestamp_utc is None:
            return datetime.now(timezone.utc)
        if isinstance(timestamp_utc, pd.Timestamp):
            ts = timestamp_utc.to_pydatetime()
        else:
            ts = timestamp_utc
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)
