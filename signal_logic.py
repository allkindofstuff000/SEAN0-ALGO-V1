from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class TradeSignal:
    timestamp_utc: pd.Timestamp
    signal_type: str
    entry_price: float
    one_h_supertrend: int
    fifteen_m_vwap: float
    reason_summary: str
    reason_lines: list[str]


@dataclass
class SignalLogic:
    """
    Strict quality-first signal logic.
    Signal is created only when every required condition is true.
    If any single condition fails, the setup is skipped.
    """

    def evaluate(self, df_15m: pd.DataFrame, df_1h: pd.DataFrame) -> tuple[TradeSignal | None, str]:
        if len(df_15m) < 120 or len(df_1h) < 120:
            return None, "Skipped: not enough candles for stable indicator checks."

        if self._has_nan_inputs(df_15m) or self._has_nan_inputs(df_1h):
            return None, "Skipped: latest indicator values contain NaN."

        buy_ok, buy_failures = self._evaluate_buy(df_15m, df_1h)
        if buy_ok:
            latest = df_15m.iloc[-1]
            return (
                TradeSignal(
                    timestamp_utc=df_15m.index[-1],
                    signal_type="CALL",
                    entry_price=float(latest["close"]),
                    one_h_supertrend=int(df_1h["supertrend_direction"].iloc[-1]),
                    fifteen_m_vwap=float(latest["session_vwap"]),
                    reason_summary=(
                        "1H bullish confluence + 15M bullish confluence + "
                        "ATR rising + outside midpoint zone"
                    ),
                    reason_lines=[
                        "1H Supertrend Green + MACD Bullish + HH Structure",
                        "15M Above VWAP + Supertrend Green + MACD Bullish + Expanding Hist",
                        "ATR Rising + Outside Range Midpoint",
                    ],
                ),
                "CALL signal passed all strict filters.",
            )

        sell_ok, sell_failures = self._evaluate_sell(df_15m, df_1h)
        if sell_ok:
            latest = df_15m.iloc[-1]
            return (
                TradeSignal(
                    timestamp_utc=df_15m.index[-1],
                    signal_type="PUT",
                    entry_price=float(latest["close"]),
                    one_h_supertrend=int(df_1h["supertrend_direction"].iloc[-1]),
                    fifteen_m_vwap=float(latest["session_vwap"]),
                    reason_summary=(
                        "1H bearish confluence + 15M bearish confluence + "
                        "ATR rising + outside midpoint zone"
                    ),
                    reason_lines=[
                        "1H Supertrend Red + MACD Bearish + LL Structure",
                        "15M Below VWAP + Supertrend Red + MACD Bearish + Expanding Hist",
                        "ATR Rising + Outside Range Midpoint",
                    ],
                ),
                "PUT signal passed all strict filters.",
            )

        buy_text = "; ".join(buy_failures)
        sell_text = "; ".join(sell_failures)
        return None, f"Skipped: BUY failed [{buy_text}] | SELL failed [{sell_text}]"

    @staticmethod
    def _has_nan_inputs(df: pd.DataFrame) -> bool:
        required = [
            "close",
            "high",
            "low",
            "session_vwap",
            "supertrend_direction",
            "macd_line",
            "macd_signal",
            "macd_hist",
            "atr_10",
            "atr_rolling_mean_20",
        ]
        latest = df[required].iloc[-3:]
        return latest.isna().any().any()

    def _evaluate_buy(self, df_15m: pd.DataFrame, df_1h: pd.DataFrame) -> tuple[bool, list[str]]:
        failures: list[str] = []
        latest_1h = df_1h.iloc[-1]
        latest_15m = df_15m.iloc[-1]
        previous_15m = df_15m.iloc[-2]

        # 1H filter 1: Supertrend must be green (direction = 1).
        if int(latest_1h["supertrend_direction"]) != 1:
            failures.append("1H Supertrend is not bullish")
        # 1H filter 2: MACD must be bullish with positive momentum.
        if not (
            latest_1h["macd_line"] > latest_1h["macd_signal"] and latest_1h["macd_hist"] > 0
        ):
            failures.append("1H MACD is not bullish")
        # 1H filter 3: Structure confirmation (higher high / stronger close).
        if not (
            df_1h["close"].iloc[-1] > df_1h["close"].iloc[-3]
            and df_1h["high"].iloc[-1] > df_1h["high"].iloc[-3]
        ):
            failures.append("1H higher-high structure missing")

        # 15M filter 1: Price must hold above BDT-session VWAP.
        if not (latest_15m["close"] > latest_15m["session_vwap"]):
            failures.append("15M close is not above VWAP")
        # 15M filter 2: Supertrend direction must align bullish.
        if int(latest_15m["supertrend_direction"]) != 1:
            failures.append("15M Supertrend is not bullish")
        # 15M filter 3: MACD line must be above signal line.
        if not (latest_15m["macd_line"] > latest_15m["macd_signal"]):
            failures.append("15M MACD line is not above signal")
        # 15M filter 4: Histogram must be expanding and positive.
        if not (
            latest_15m["macd_hist"] > previous_15m["macd_hist"] and latest_15m["macd_hist"] > 0
        ):
            failures.append("15M MACD histogram not expanding bullish")
        # 15M filter 5: Current volatility must be above recent baseline.
        if not (latest_15m["atr_10"] > latest_15m["atr_rolling_mean_20"]):
            failures.append("15M ATR is not above rolling ATR average")
        # 15M filter 6: Avoid entries in midpoint chop zone.
        if self._is_inside_midpoint_zone(df_15m):
            failures.append("15M entry is inside central midpoint zone")
        return len(failures) == 0, failures

    def _evaluate_sell(self, df_15m: pd.DataFrame, df_1h: pd.DataFrame) -> tuple[bool, list[str]]:
        failures: list[str] = []
        latest_1h = df_1h.iloc[-1]
        latest_15m = df_15m.iloc[-1]
        previous_15m = df_15m.iloc[-2]

        # 1H filter 1: Supertrend must be red (direction = -1).
        if int(latest_1h["supertrend_direction"]) != -1:
            failures.append("1H Supertrend is not bearish")
        # 1H filter 2: MACD must be bearish with negative momentum.
        if not (
            latest_1h["macd_line"] < latest_1h["macd_signal"] and latest_1h["macd_hist"] < 0
        ):
            failures.append("1H MACD is not bearish")
        # 1H filter 3: Structure confirmation (lower low / weaker close).
        if not (
            df_1h["close"].iloc[-1] < df_1h["close"].iloc[-3]
            and df_1h["low"].iloc[-1] < df_1h["low"].iloc[-3]
        ):
            failures.append("1H lower-low structure missing")

        # 15M filter 1: Price must hold below BDT-session VWAP.
        if not (latest_15m["close"] < latest_15m["session_vwap"]):
            failures.append("15M close is not below VWAP")
        # 15M filter 2: Supertrend direction must align bearish.
        if int(latest_15m["supertrend_direction"]) != -1:
            failures.append("15M Supertrend is not bearish")
        # 15M filter 3: MACD line must be below signal line.
        if not (latest_15m["macd_line"] < latest_15m["macd_signal"]):
            failures.append("15M MACD line is not below signal")
        # 15M filter 4: Histogram must be expanding and negative.
        if not (
            latest_15m["macd_hist"] < previous_15m["macd_hist"] and latest_15m["macd_hist"] < 0
        ):
            failures.append("15M MACD histogram not expanding bearish")
        # 15M filter 5: Current volatility must be above recent baseline.
        if not (latest_15m["atr_10"] > latest_15m["atr_rolling_mean_20"]):
            failures.append("15M ATR is not above rolling ATR average")
        # 15M filter 6: Avoid entries in midpoint chop zone.
        if self._is_inside_midpoint_zone(df_15m):
            failures.append("15M entry is inside central midpoint zone")
        return len(failures) == 0, failures

    @staticmethod
    def _is_inside_midpoint_zone(df_15m: pd.DataFrame) -> bool:
        # Last 20-candle range (excluding current candle by using iloc[-20:-1]).
        lookback_highs = df_15m["high"].iloc[-20:-1]
        lookback_lows = df_15m["low"].iloc[-20:-1]
        range_high = float(lookback_highs.max())
        range_low = float(lookback_lows.min())
        range_width = range_high - range_low
        if range_width <= 0:
            return True

        # Central-zone filter: avoid entries in the center 30% of the recent range.
        midpoint = (range_high + range_low) / 2.0
        close_price = float(df_15m["close"].iloc[-1])
        return abs(close_price - midpoint) < (range_width * 0.15)
