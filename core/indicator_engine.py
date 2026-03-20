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
    Minimal indicator engine for the MVP strategy set.

    Indicators:
    - EMA50
    - EMA200
    - RSI14
    - ATR14
    - ATR 20-period average
    """

    ema_fast: int = 50
    ema_slow: int = 200
    rsi_length: int = 14
    atr_length: int = 14
    atr_average_window: int = 20

    def add_indicators(self, candles: pd.DataFrame) -> pd.DataFrame:
        if candles is None or candles.empty:
            return candles.copy()

        required = {"timestamp", "open", "high", "low", "close", "volume"}
        missing = required - set(candles.columns)
        if missing:
            raise ValueError(f"Missing required candle columns: {sorted(missing)}")

        out = candles.copy()
        out["ema50"] = out["close"].ewm(span=self.ema_fast, adjust=False).mean()
        out["ema200"] = out["close"].ewm(span=self.ema_slow, adjust=False).mean()

        if ta is not None:
            out["rsi14"] = ta.rsi(out["close"], length=self.rsi_length)
            out["atr14"] = ta.atr(
                high=out["high"],
                low=out["low"],
                close=out["close"],
                length=self.atr_length,
            )
        else:
            LOGGER.info("pandas_ta_missing using_manual_indicators")
            out["rsi14"] = self._manual_rsi(out["close"], self.rsi_length)
            out["atr14"] = self._manual_atr(out)

        out["atr20_avg"] = out["atr14"].rolling(self.atr_average_window, min_periods=self.atr_average_window).mean()
        out["atr_expanding"] = out["atr14"] > out["atr20_avg"]
        return out

    def _manual_atr(self, candles: pd.DataFrame) -> pd.Series:
        previous_close = candles["close"].shift(1)
        true_range = pd.concat(
            [
                candles["high"] - candles["low"],
                (candles["high"] - previous_close).abs(),
                (candles["low"] - previous_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        return true_range.rolling(self.atr_length, min_periods=self.atr_length).mean()

    @staticmethod
    def _manual_rsi(close: pd.Series, length: int) -> pd.Series:
        delta = close.diff()
        gains = delta.clip(lower=0)
        losses = -delta.clip(upper=0)
        avg_gain = gains.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
        avg_loss = losses.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, pd.NA)
        rsi = 100 - (100 / (1 + rs))
        return rsi.fillna(50.0)
