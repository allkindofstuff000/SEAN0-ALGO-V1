from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd


LOGGER = logging.getLogger(__name__)


@dataclass
class RegimeDetector:
    """
    Classify the market regime using EMA alignment, ATR expansion, and range
    compression/breakout behaviour.
    """

    compression_lookback: int = 20
    breakout_lookback: int = 20
    trending_spread_threshold: float = 0.0010
    breakout_atr_ratio: float = 1.20
    compression_threshold: float = 0.0060

    def detect(self, df: pd.DataFrame) -> dict[str, object]:
        required = {"close", "high", "low", "ema20", "ema50", "atr14"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"Regime detector requires columns: {sorted(missing)}")
        if len(df) < max(self.compression_lookback, self.breakout_lookback) + 2:
            raise ValueError("Not enough candles for regime detection.")

        last = df.iloc[-1]
        previous = df.iloc[-2]
        close = float(last["close"])
        ema20 = float(last["ema20"])
        ema50 = float(last["ema50"])
        atr_now = float(last["atr14"])
        atr_mean = float(df["atr14"].tail(self.compression_lookback).mean())
        atr_ratio = atr_now / atr_mean if atr_mean > 0 else 1.0
        atr_expansion = atr_ratio >= 1.0

        ema_spread = abs(ema20 - ema50) / close if close else 0.0
        range_high = float(df["high"].iloc[-self.breakout_lookback - 1 : -1].max())
        range_low = float(df["low"].iloc[-self.breakout_lookback - 1 : -1].min())
        range_width = (range_high - range_low) / close if close else 0.0
        compressed = range_width <= self.compression_threshold

        breakout_up = bool(float(last["close"]) > range_high and float(previous["close"]) <= range_high)
        breakout_down = bool(float(last["close"]) < range_low and float(previous["close"]) >= range_low)
        ema_alignment = "BULLISH" if ema20 >= ema50 else "BEARISH"

        if compressed and atr_ratio >= self.breakout_atr_ratio and (breakout_up or breakout_down):
            regime = "BREAKOUT"
        elif ema_spread >= self.trending_spread_threshold and atr_expansion:
            regime = "TRENDING"
        else:
            regime = "RANGING"

        result = {
            "regime": regime,
            "ema_alignment": ema_alignment,
            "atr_ratio": round(atr_ratio, 4),
            "atr_expansion": atr_expansion,
            "range_width": round(range_width, 6),
            "compressed": compressed,
            "breakout_up": breakout_up,
            "breakout_down": breakout_down,
            "range_high": round(range_high, 4),
            "range_low": round(range_low, 4),
        }
        LOGGER.debug("regime_result=%s", result)
        return result
