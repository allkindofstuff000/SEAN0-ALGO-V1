from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import pandas_ta as ta


@dataclass
class IndicatorEngine:
    """
    Computes strategy indicators on CLOSED candles only.
    Input DataFrame must use UTC datetime index and include OHLCV columns.
    """

    atr_length: int = 10
    supertrend_multiplier: float = 3.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    atr_rolling_window: int = 20
    bdt_timezone: str = "Asia/Dhaka"

    def add_indicators(self, candles: pd.DataFrame) -> pd.DataFrame:
        if candles.empty:
            return candles.copy()

        df = candles.copy()
        df["session_vwap"] = self._daily_session_vwap(df)

        df["atr_10"] = ta.atr(
            high=df["high"],
            low=df["low"],
            close=df["close"],
            length=self.atr_length,
        )
        df["atr_rolling_mean_20"] = df["atr_10"].rolling(self.atr_rolling_window).mean()

        macd = ta.macd(
            close=df["close"],
            fast=self.macd_fast,
            slow=self.macd_slow,
            signal=self.macd_signal,
        )
        if macd is None or macd.empty:
            raise RuntimeError("MACD calculation failed. Check candle data integrity.")

        df["macd_line"] = macd.iloc[:, 0]
        df["macd_hist"] = macd.iloc[:, 1]
        df["macd_signal"] = macd.iloc[:, 2]

        supertrend = ta.supertrend(
            high=df["high"],
            low=df["low"],
            close=df["close"],
            length=self.atr_length,
            multiplier=self.supertrend_multiplier,
        )
        if supertrend is None or supertrend.empty:
            raise RuntimeError("Supertrend calculation failed. Check candle data integrity.")

        direction_column = [col for col in supertrend.columns if col.startswith("SUPERTd_")]
        trend_column = [col for col in supertrend.columns if col.startswith("SUPERT_")]
        if not direction_column or not trend_column:
            raise RuntimeError("Unexpected Supertrend output columns.")

        # Early rows can be NaN while indicators warm up; keep nullable Int64 to avoid cast crashes.
        df["supertrend_direction"] = pd.to_numeric(
            supertrend[direction_column[0]], errors="coerce"
        ).astype("Int64")
        df["supertrend_value"] = supertrend[trend_column[0]]
        return df

    def _daily_session_vwap(self, df: pd.DataFrame) -> pd.Series:
        # VWAP reset at 00:00 BDT, then cumulative within each local date.
        local_dates = df.index.tz_convert(self.bdt_timezone).date
        typical_price = (df["high"] + df["low"] + df["close"]) / 3.0
        price_volume = typical_price * df["volume"]
        cumulative_pv = price_volume.groupby(local_dates).cumsum()
        cumulative_vol = df["volume"].groupby(local_dates).cumsum()
        return cumulative_pv / cumulative_vol.replace(0, np.nan)
