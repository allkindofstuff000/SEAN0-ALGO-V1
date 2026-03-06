from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

try:
    import pandas_ta as ta
except Exception:  # pragma: no cover - graceful fallback when pandas_ta missing
    ta = None


LOGGER = logging.getLogger(__name__)


@dataclass
class IndicatorEngine:
    """
    Indicator pipeline for phase-1 market data.
    Computes EMA20, EMA50, VWAP, ATR14, and MACD(12,26,9).
    """

    ema_fast: int = 20
    ema_slow: int = 50
    atr_length: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9

    def add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return df.copy()

        required = {"open", "high", "low", "close", "volume"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"DataFrame missing columns for indicators: {sorted(missing)}")

        out = df.copy()

        out["ema20"] = out["close"].ewm(span=self.ema_fast, adjust=False).mean()
        out["ema50"] = out["close"].ewm(span=self.ema_slow, adjust=False).mean()

        typical = (out["high"] + out["low"] + out["close"]) / 3.0
        cum_pv = (typical * out["volume"]).cumsum()
        cum_v = out["volume"].replace(0, np.nan).cumsum()
        out["vwap"] = cum_pv / cum_v

        if ta is not None:
            out["atr14"] = ta.atr(high=out["high"], low=out["low"], close=out["close"], length=self.atr_length)
            macd = ta.macd(
                close=out["close"],
                fast=self.macd_fast,
                slow=self.macd_slow,
                signal=self.macd_signal,
            )
            if macd is not None and not macd.empty:
                out["macd"] = macd.iloc[:, 0]
                out["macd_hist"] = macd.iloc[:, 1]
                out["macd_signal"] = macd.iloc[:, 2]
            else:
                self._fallback_macd(out)
        else:
            LOGGER.info("pandas_ta not available, using fallback ATR/MACD implementation")
            self._fallback_atr(out)
            self._fallback_macd(out)

        return out

    def _fallback_atr(self, out: pd.DataFrame) -> None:
        prev_close = out["close"].shift(1)
        tr = pd.concat(
            [
                out["high"] - out["low"],
                (out["high"] - prev_close).abs(),
                (out["low"] - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        out["atr14"] = tr.rolling(self.atr_length, min_periods=self.atr_length).mean()

    def _fallback_macd(self, out: pd.DataFrame) -> None:
        ema_fast = out["close"].ewm(span=self.macd_fast, adjust=False).mean()
        ema_slow = out["close"].ewm(span=self.macd_slow, adjust=False).mean()
        out["macd"] = ema_fast - ema_slow
        out["macd_signal"] = out["macd"].ewm(span=self.macd_signal, adjust=False).mean()
        out["macd_hist"] = out["macd"] - out["macd_signal"]
