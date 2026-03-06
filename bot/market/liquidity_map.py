from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import pandas as pd


LOGGER = logging.getLogger(__name__)


@dataclass
class LiquidityMapEngine:
    """
    Build structural liquidity context from closed candles.

    The engine detects equal highs/lows, recent swing points, and whether the
    latest candle performed a range liquidity sweep.
    """

    lookback: int = 80
    swing_window: int = 2
    equal_level_tolerance_bps: float = 4.0

    def build(self, df: pd.DataFrame) -> dict[str, Any]:
        required = {"high", "low", "close"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"Liquidity map requires columns: {sorted(missing)}")
        if len(df) < max(self.lookback, self.swing_window * 2 + 3):
            raise ValueError("Not enough candles for liquidity-map analysis.")

        recent = df.tail(self.lookback).reset_index(drop=True)
        highs = recent["high"].astype(float)
        lows = recent["low"].astype(float)
        closes = recent["close"].astype(float)

        tolerance = float(closes.iloc[-1]) * (self.equal_level_tolerance_bps / 10_000.0)
        equal_highs = self._detect_equal_levels(highs, tolerance)
        equal_lows = self._detect_equal_levels(lows, tolerance)
        swing_highs, swing_lows = self._detect_swings(recent)

        last = recent.iloc[-1]
        previous_high = float(highs.iloc[:-1].max())
        previous_low = float(lows.iloc[:-1].min())
        bearish_sweep = bool(float(last["high"]) > previous_high and float(last["close"]) < previous_high)
        bullish_sweep = bool(float(last["low"]) < previous_low and float(last["close"]) > previous_low)

        result = {
            "equal_highs": equal_highs,
            "equal_lows": equal_lows,
            "swing_highs": swing_highs,
            "swing_lows": swing_lows,
            "range_high": previous_high,
            "range_low": previous_low,
            "bullish_sweep": bullish_sweep,
            "bearish_sweep": bearish_sweep,
            "last_sweep": self._resolve_last_sweep(bullish_sweep=bullish_sweep, bearish_sweep=bearish_sweep),
        }
        LOGGER.debug("liquidity_map=%s", result)
        return result

    @staticmethod
    def _resolve_last_sweep(*, bullish_sweep: bool, bearish_sweep: bool) -> str | None:
        if bullish_sweep:
            return "BULLISH"
        if bearish_sweep:
            return "BEARISH"
        return None

    @staticmethod
    def _detect_equal_levels(series: pd.Series, tolerance: float) -> list[float]:
        values = sorted(float(value) for value in series.dropna().tolist())
        if not values:
            return []

        levels: list[float] = []
        cluster: list[float] = []
        for value in values:
            if not cluster:
                cluster = [value]
                continue
            if abs(value - cluster[-1]) <= tolerance:
                cluster.append(value)
                continue
            if len(cluster) >= 2:
                levels.append(round(sum(cluster) / len(cluster), 4))
            cluster = [value]

        if len(cluster) >= 2:
            levels.append(round(sum(cluster) / len(cluster), 4))

        deduped: list[float] = []
        for level in levels:
            if not deduped or abs(level - deduped[-1]) > tolerance:
                deduped.append(level)
        return deduped

    def _detect_swings(self, df: pd.DataFrame) -> tuple[list[float], list[float]]:
        highs: list[float] = []
        lows: list[float] = []
        window = self.swing_window

        for index in range(window, len(df) - window):
            center_high = float(df.loc[index, "high"])
            center_low = float(df.loc[index, "low"])
            left_high = df.loc[index - window : index - 1, "high"].astype(float)
            right_high = df.loc[index + 1 : index + window, "high"].astype(float)
            left_low = df.loc[index - window : index - 1, "low"].astype(float)
            right_low = df.loc[index + 1 : index + window, "low"].astype(float)

            if center_high >= float(left_high.max()) and center_high >= float(right_high.max()):
                highs.append(round(center_high, 4))
            if center_low <= float(left_low.min()) and center_low <= float(right_low.min()):
                lows.append(round(center_low, 4))

        return highs[-10:], lows[-10:]
