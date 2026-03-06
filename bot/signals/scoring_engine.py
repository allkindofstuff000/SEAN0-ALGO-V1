from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import pandas as pd


LOGGER = logging.getLogger(__name__)


@dataclass
class SignalScoringEngine:
    """
    Weighted scoring engine for CALL/PUT setups.

    Score components:
    - trend_alignment: 25
    - liquidity_sweep: 20
    - atr_expansion: 15
    - momentum_candle: 10
    - session_strength: 10
    - regime_alignment: 20
    """

    weight_trend_alignment: int = 25
    weight_liquidity_sweep: int = 20
    weight_atr_expansion: int = 15
    weight_momentum_candle: int = 10
    weight_session_strength: int = 10
    weight_regime_alignment: int = 20

    def score(
        self,
        *,
        df: pd.DataFrame,
        liquidity_map: dict[str, Any],
        regime: dict[str, Any],
        session: dict[str, Any],
    ) -> dict[str, Any]:
        if len(df) < 30:
            raise ValueError("Need at least 30 rows for scoring.")

        last = df.iloc[-1]
        open_price = float(last["open"])
        close_price = float(last["close"])
        ema20 = float(last["ema20"])
        ema50 = float(last["ema50"])
        vwap = float(last.get("vwap", close_price))
        macd = float(last.get("macd", 0.0))
        macd_signal = float(last.get("macd_signal", 0.0))
        atr_now = float(last.get("atr14", 0.0))
        atr_mean = float(df["atr14"].tail(20).mean())
        atr_ratio = atr_now / atr_mean if atr_mean > 0 else 1.0
        body = close_price - open_price

        call = self._empty_breakdown()
        put = self._empty_breakdown()

        bullish_trend = ema20 > ema50 and macd >= macd_signal and close_price >= vwap
        bearish_trend = ema20 < ema50 and macd <= macd_signal and close_price <= vwap
        if bullish_trend:
            call["trend_alignment"] = self.weight_trend_alignment
        if bearish_trend:
            put["trend_alignment"] = self.weight_trend_alignment

        if bool(liquidity_map.get("bullish_sweep")):
            call["liquidity_sweep"] = self.weight_liquidity_sweep
        if bool(liquidity_map.get("bearish_sweep")):
            put["liquidity_sweep"] = self.weight_liquidity_sweep

        atr_points = self._scaled_points(
            value=atr_ratio,
            base=1.0,
            full=1.3,
            max_points=self.weight_atr_expansion,
        )
        call["atr_expansion"] = atr_points
        put["atr_expansion"] = atr_points

        median_body = float((df["close"] - df["open"]).abs().tail(20).median())
        strong_body = abs(body) >= (median_body * 1.2 if median_body > 0 else 0.0)
        if strong_body and body > 0:
            call["momentum_candle"] = self.weight_momentum_candle
        if strong_body and body < 0:
            put["momentum_candle"] = self.weight_momentum_candle

        session_strength_points = int(round(float(session.get("strength", 0.0)) * self.weight_session_strength))
        session_strength_points = max(0, min(self.weight_session_strength, session_strength_points))
        call["session_strength"] = session_strength_points
        put["session_strength"] = session_strength_points

        regime_name = str(regime.get("regime", "RANGING")).upper()
        breakout_up = bool(regime.get("breakout_up", False))
        breakout_down = bool(regime.get("breakout_down", False))
        if regime_name == "TRENDING":
            if bullish_trend:
                call["regime_alignment"] = self.weight_regime_alignment
            if bearish_trend:
                put["regime_alignment"] = self.weight_regime_alignment
        elif regime_name == "BREAKOUT":
            if breakout_up:
                call["regime_alignment"] = self.weight_regime_alignment
            if breakout_down:
                put["regime_alignment"] = self.weight_regime_alignment
        else:
            if bool(liquidity_map.get("bullish_sweep")):
                call["regime_alignment"] = self.weight_regime_alignment
            if bool(liquidity_map.get("bearish_sweep")):
                put["regime_alignment"] = self.weight_regime_alignment

        call_score = int(sum(call.values()))
        put_score = int(sum(put.values()))
        direction = self._select_direction(
            call_score=call_score,
            put_score=put_score,
            bullish_trend=bullish_trend,
            bearish_trend=bearish_trend,
            candle_body=body,
        )
        selected_breakdown = call if direction == "CALL" else put
        selected_score = call_score if direction == "CALL" else put_score

        result = {
            "direction": direction,
            "score": selected_score,
            "call_score": call_score,
            "put_score": put_score,
            "breakdown": selected_breakdown,
            "call_breakdown": call,
            "put_breakdown": put,
            "atr_ratio": round(atr_ratio, 4),
            "atr": atr_now,
            "session": str(session.get("session", "ASIAN")),
            "regime": regime_name,
        }
        LOGGER.debug("score_result=%s", result)
        return result

    @staticmethod
    def _empty_breakdown() -> dict[str, int]:
        return {
            "trend_alignment": 0,
            "liquidity_sweep": 0,
            "atr_expansion": 0,
            "momentum_candle": 0,
            "session_strength": 0,
            "regime_alignment": 0,
        }

    @staticmethod
    def _scaled_points(*, value: float, base: float, full: float, max_points: int) -> int:
        if value <= base:
            return 0
        if value >= full:
            return max_points
        ratio = (value - base) / (full - base)
        return int(round(max_points * ratio))

    @staticmethod
    def _select_direction(
        *,
        call_score: int,
        put_score: int,
        bullish_trend: bool,
        bearish_trend: bool,
        candle_body: float,
    ) -> str:
        if call_score > put_score:
            return "CALL"
        if put_score > call_score:
            return "PUT"
        if bullish_trend and not bearish_trend:
            return "CALL"
        if bearish_trend and not bullish_trend:
            return "PUT"
        if candle_body < 0:
            return "PUT"
        return "CALL"
