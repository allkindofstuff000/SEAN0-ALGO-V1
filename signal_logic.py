from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from typing import Any

import pandas as pd

from decision_logger import DecisionLogger, get_decision_logger


@dataclass
class TradeSignal:
    """Signal payload used by the simplified XAUUSD runtime."""

    timestamp_utc: pd.Timestamp
    symbol: str
    signal_type: str
    entry_price: float
    score: int
    score_threshold: int
    atr: float
    expiry_minutes: int = 5
    stop_loss: float | None = None
    take_profit: float | None = None
    session: str = "UNKNOWN"
    reason_summary: str = ""

    @property
    def forex_direction(self) -> str:
        return "BUY" if self.signal_type == "CALL" else "SELL"

    @property
    def binary_direction(self) -> str:
        return "UP" if self.signal_type == "CALL" else "DOWN"

    def binary_message(self) -> str:
        return f"{self.symbol} Binary {self.binary_direction} | Expiry {self.expiry_minutes}min | Score {self.score}"

    def forex_message(self) -> str:
        stop_loss = "-" if self.stop_loss is None else f"{self.stop_loss:.1f}"
        take_profit = "-" if self.take_profit is None else f"{self.take_profit:.1f}"
        return (
            f"{self.symbol} {self.forex_direction} | "
            f"Entry {self.entry_price:.1f} | "
            f"SL {stop_loss} | "
            f"TP {take_profit} | "
            f"Score {self.score}"
        )


@dataclass
class SignalDecision:
    candle_time_utc: pd.Timestamp
    symbol: str
    session: str
    score: int
    score_threshold: int
    direction: str
    trend_alignment: bool
    atr_expansion: bool
    session_filter: bool
    signal_generated: bool
    reason: str
    breakdown: dict[str, int]
    signal: TradeSignal | None = None


@dataclass
class SignalLogic:
    """
    Simplified XAUUSD signal engine.

    Score model:
    - trend_alignment: 50
    - atr_expansion: 25
    - session_filter: 25
    """

    symbol: str = "XAUUSD"
    threshold: int = 70
    trend_weight: int = 50
    atr_weight: int = 25
    session_weight: int = 25
    atr_expansion_ratio: float = 1.05
    binary_expiry_minutes: int = 5
    forex_sl_atr_multiplier: float = 1.5
    forex_tp_atr_multiplier: float = 3.0
    decision_logger: DecisionLogger = field(default_factory=get_decision_logger)

    def evaluate(self, candles: pd.DataFrame) -> SignalDecision:
        if candles is None or candles.empty:
            raise ValueError("No candle data available for signal evaluation.")
        if len(candles) < 60:
            raise ValueError("Need at least 60 candles to evaluate a signal reliably.")

        last = candles.iloc[-1]
        timestamp = self._as_timestamp(last["timestamp"])
        close_price = float(last["close"])
        ema_fast = float(last["ema20"])
        ema_slow = float(last["ema50"])
        atr_value = float(last["atr14"])
        atr_ratio = float(last.get("atr_ratio", 0.0) or 0.0)
        session = self._detect_session(timestamp)

        trend_alignment, direction = self._trend_alignment(ema_fast=ema_fast, ema_slow=ema_slow)
        atr_expansion = atr_ratio >= self.atr_expansion_ratio
        session_filter = session in {"LONDON", "OVERLAP", "NEW_YORK"}

        breakdown = {
            "trend_alignment": self.trend_weight if trend_alignment else 0,
            "atr_expansion": self.atr_weight if atr_expansion else 0,
            "session_filter": self.session_weight if session_filter else 0,
        }
        score = int(sum(breakdown.values()))
        signal_generated = bool(direction != "NONE" and score >= self.threshold)

        failed_checks = [name for name, passed in {
            "trend_alignment": trend_alignment,
            "atr_expansion": atr_expansion,
            "session_filter": session_filter,
        }.items() if not passed]

        if signal_generated:
            reason = "accepted"
        elif direction == "NONE":
            reason = "rejected:no_trend_alignment"
        else:
            reason = f"rejected:{','.join(failed_checks)}" if failed_checks else "rejected:score_below_threshold"

        signal: TradeSignal | None = None
        if signal_generated:
            signal = self._build_trade_signal(
                timestamp=timestamp,
                close_price=close_price,
                atr_value=atr_value,
                direction=direction,
                session=session,
                score=score,
            )

        decision = SignalDecision(
            candle_time_utc=timestamp,
            symbol=self.symbol,
            session=session,
            score=score,
            score_threshold=self.threshold,
            direction=direction,
            trend_alignment=trend_alignment,
            atr_expansion=atr_expansion,
            session_filter=session_filter,
            signal_generated=signal_generated,
            reason=reason,
            breakdown=breakdown,
            signal=signal,
        )
        self._log_decision(
            decision=decision,
            price=close_price,
            atr_ratio=atr_ratio,
        )
        return decision

    def _build_trade_signal(
        self,
        *,
        timestamp: pd.Timestamp,
        close_price: float,
        atr_value: float,
        direction: str,
        session: str,
        score: int,
    ) -> TradeSignal:
        signal_type = "CALL" if direction == "BULLISH" else "PUT"
        if signal_type == "CALL":
            stop_loss = close_price - (atr_value * self.forex_sl_atr_multiplier)
            take_profit = close_price + (atr_value * self.forex_tp_atr_multiplier)
        else:
            stop_loss = close_price + (atr_value * self.forex_sl_atr_multiplier)
            take_profit = close_price - (atr_value * self.forex_tp_atr_multiplier)

        return TradeSignal(
            timestamp_utc=timestamp,
            symbol=self.symbol,
            signal_type=signal_type,
            entry_price=close_price,
            score=score,
            score_threshold=self.threshold,
            atr=atr_value,
            expiry_minutes=self.binary_expiry_minutes,
            stop_loss=round(stop_loss, 4),
            take_profit=round(take_profit, 4),
            session=session,
            reason_summary="EMA trend + ATR expansion + active session",
        )

    def _log_decision(self, *, decision: SignalDecision, price: float, atr_ratio: float) -> None:
        self.decision_logger.log_decision(
            {
                "timestamp": decision.candle_time_utc,
                "price": price,
                "session": decision.session,
                "trend_alignment": decision.trend_alignment,
                "liquidity_sweep": False,
                "atr_expansion": decision.atr_expansion,
                "market_regime": "MVP",
                "signal_score": decision.score,
                "score_threshold": decision.score_threshold,
                "signal_generated": decision.signal_generated,
                "reason": decision.reason,
                "direction": decision.direction,
                "symbol": decision.symbol,
                "atr_ratio": round(atr_ratio, 4),
                "breakdown": decision.breakdown,
            }
        )

    @staticmethod
    def _trend_alignment(*, ema_fast: float, ema_slow: float) -> tuple[bool, str]:
        if ema_fast > ema_slow:
            return True, "BULLISH"
        if ema_fast < ema_slow:
            return True, "BEARISH"
        return False, "NONE"

    @staticmethod
    def _as_timestamp(value: Any) -> pd.Timestamp:
        if isinstance(value, pd.Timestamp):
            ts = value
        else:
            ts = pd.Timestamp(value)
        if ts.tzinfo is None:
            return ts.tz_localize(timezone.utc)
        return ts.tz_convert(timezone.utc)

    def _detect_session(self, timestamp_utc: pd.Timestamp) -> str:
        current_time = timestamp_utc.tz_convert(timezone.utc).time()
        if time(7, 0) <= current_time < time(12, 0):
            return "LONDON"
        if time(12, 0) <= current_time < time(16, 0):
            return "OVERLAP"
        if time(16, 0) <= current_time < time(21, 0):
            return "NEW_YORK"
        return "ASIAN"
