from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

try:
    import pandas_ta as ta
except Exception:  # pragma: no cover - fallback path
    ta = None


LOGGER = logging.getLogger(__name__)


@dataclass
class IndicatorEngine:
    """
    Minimal indicator pipeline for the XAUUSD MVP.

    Indicators:
    - EMA20
    - EMA50
    - ATR14
    """

    ema_fast: int = 20
    ema_slow: int = 50
    atr_length: int = 14
    atr_baseline_window: int = 20

    def add_indicators(self, candles: pd.DataFrame) -> pd.DataFrame:
        if candles is None or candles.empty:
            return candles.copy()

        required = {"timestamp", "open", "high", "low", "close", "volume"}
        missing = required - set(candles.columns)
        if missing:
            raise ValueError(f"Missing required candle columns: {sorted(missing)}")

        out = candles.copy()
        out["ema20"] = out["close"].ewm(span=self.ema_fast, adjust=False).mean()
        out["ema50"] = out["close"].ewm(span=self.ema_slow, adjust=False).mean()

        if ta is not None:
            out["atr14"] = ta.atr(
                high=out["high"],
                low=out["low"],
                close=out["close"],
                length=self.atr_length,
            )
        else:
            LOGGER.info("pandas_ta_missing using_manual_atr")
            out["atr14"] = self._manual_atr(out)

        out["atr_baseline"] = out["atr14"].rolling(self.atr_baseline_window, min_periods=self.atr_baseline_window).mean()
        out["atr_ratio"] = out["atr14"] / out["atr_baseline"]
        out["atr_expanding"] = out["atr_ratio"] > 1.0
        return out

    def _manual_atr(self, out: pd.DataFrame) -> pd.Series:
        previous_close = out["close"].shift(1)
        true_range = pd.concat(
            [
                out["high"] - out["low"],
                (out["high"] - previous_close).abs(),
                (out["low"] - previous_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        return true_range.rolling(self.atr_length, min_periods=self.atr_length).mean()
