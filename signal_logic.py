from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
import pytz

from decision_logger import DecisionLogger, get_decision_logger
from trade_filters import run_trade_filters


XAU_SYMBOLS = {"XAUUSD", "XAUUSDT"}


@dataclass
class TradeSignal:
    """Standardized XAU signal object used by the simplified MVP engine."""

    timestamp_utc: pd.Timestamp
    symbol: str
    direction: str
    score: int
    score_threshold: int
    entry_price: float
    stop_loss: float | None
    take_profit: float | None
    signal_kind: str
    trend_timeframe: str
    entry_timeframe: str
    atr: float
    expiry_minutes: int | None = None
    reason_summary: str = ""
    session: str = "UNKNOWN"

    @property
    def message_format(self) -> str:
        return self.signal_kind.upper()

    @property
    def display_symbol(self) -> str:
        return "XAUUSD"

    @property
    def signal_type(self) -> str:
        if self.signal_kind == "binary":
            return "CALL" if self.direction == "BUY" else "PUT"
        return self.direction

    @property
    def entry_timeframe_minutes(self) -> int:
        suffix = self.entry_timeframe[-1].lower()
        value = int(self.entry_timeframe[:-1])
        if suffix == "m":
            return value
        if suffix == "h":
            return value * 60
        raise ValueError(f"Unsupported timeframe: {self.entry_timeframe}")

    def binary_message(self) -> str:
        binary_direction = "UP" if self.direction == "BUY" else "DOWN"
        expiry = self.expiry_minutes if self.expiry_minutes is not None else self.entry_timeframe_minutes
        return (
            f"{self.display_symbol} Binary {binary_direction}\n"
            f"Expiry: {expiry}m\n"
            f"Score: {self.score}"
        )

    def forex_message(self) -> str:
        stop_loss = "-" if self.stop_loss is None else self._format_price(self.stop_loss)
        take_profit = "-" if self.take_profit is None else self._format_price(self.take_profit)
        return (
            f"{self.display_symbol} {self.direction}\n"
            f"Entry: {self._format_price(self.entry_price)}\n"
            f"SL: {stop_loss}\n"
            f"TP: {take_profit}"
        )

    def message(self) -> str:
        if self.signal_kind == "binary":
            return self.binary_message()
        return self.forex_message()

    @staticmethod
    def _format_price(value: float) -> str:
        text = f"{value:.2f}"
        return text.rstrip("0").rstrip(".")


@dataclass
class SignalDecision:
    candle_time_utc: pd.Timestamp
    symbol: str
    strategy: str
    session: str
    score: int
    score_threshold: int
    direction: str
    trend_alignment: bool
    price_trigger: bool
    rsi_filter: bool
    atr_expansion: bool
    session_filter: bool
    signal_generated: bool
    reason: str
    breakdown: dict[str, int]
    signal: TradeSignal | None = None
    signals: list[TradeSignal] = field(default_factory=list)


@dataclass
class SignalLogic:
    """Single-strategy XAUUSD signal engine with binary and forex outputs."""

    symbol: str = "XAUUSDT"
    threshold: int = 80
    rule_weight: int = 20
    signal_modes: tuple[str, ...] = ("binary", "forex")
    forex_sl_atr_multiplier: float = 1.5
    forex_tp_atr_multiplier: float = 3.0
    decision_logger: DecisionLogger = field(default_factory=get_decision_logger)

    def evaluate(
        self,
        trend_candles: pd.DataFrame,
        entry_candles: pd.DataFrame | None = None,
        now_utc: datetime.datetime | None = None,
    ) -> SignalDecision:
        if trend_candles is None or trend_candles.empty:
            raise ValueError("No trend candle data available for signal evaluation.")
        entry_candles = trend_candles if entry_candles is None else entry_candles
        if entry_candles.empty:
            raise ValueError("No entry candle data available for signal evaluation.")

        normalized_symbol = self._normalize_symbol(self.symbol)
        if normalized_symbol not in XAU_SYMBOLS:
            raise ValueError(f"Unsupported symbol for XAU strategy: {self.symbol}")

        current_utc = now_utc.astimezone(pytz.UTC) if now_utc is not None else datetime.datetime.now(pytz.UTC)
        return self.evaluate_xau_strategy(trend_candles, entry_candles, current_utc)

    def evaluate_xau_strategy(
        self,
        trend_candles: pd.DataFrame,
        entry_candles: pd.DataFrame,
        now_utc: datetime.datetime,
    ) -> SignalDecision:
        self._ensure_minimum_rows(trend_candles, entry_candles)
        trend_last = trend_candles.iloc[-1]
        entry_last = entry_candles.iloc[-1]
        entry_prev = entry_candles.iloc[-2]
        candle_time = self._as_timestamp(entry_last["timestamp"])
        session = self._detect_session(now_utc)
        trend_bias = self.decision_logger.log_trend(
            float(trend_last["ema50"]),
            float(trend_last["ema200"]),
        )
        bullish_trend = trend_bias == "bull"
        bearish_trend = trend_bias == "bear"
        trend_alignment = bullish_trend or bearish_trend
        session_filter = self.decision_logger.log_session(
            session,
            session in {"LONDON", "OVERLAP", "NEW_YORK"},
        )

        if bullish_trend:
            direction = "BUY"
        elif bearish_trend:
            direction = "SELL"
        else:
            direction = "NONE"
        price_trigger = self.decision_logger.log_breakout(
            float(entry_last["close"]),
            float(entry_prev["high"]),
            float(entry_prev["low"]),
            trend_bias,
        )
        rsi_filter = self.decision_logger.log_rsi(
            float(entry_last["rsi14"]),
            trend_bias,
            buy_threshold=55.0,
            sell_threshold=45.0,
        )
        atr_expansion = self.decision_logger.log_volatility(
            float(entry_last["atr14"]),
            float(entry_last.get("atr20_avg", 0.0) or 0.0),
        )
        breakdown = self._score_breakdown(
            trend_alignment=trend_alignment,
            price_trigger=price_trigger,
            rsi_filter=rsi_filter,
            atr_expansion=atr_expansion,
        )
        score = int(sum(breakdown.values()))
        core_signal_ready = bool(
            direction != "NONE"
            and session_filter
            and trend_alignment
            and price_trigger
            and rsi_filter
            and atr_expansion
            and score >= self.threshold
        )

        reason = self._build_reason(
            direction=direction,
            checks={
                "session_filter": session_filter,
                "trend_alignment": trend_alignment,
                "price_break": price_trigger,
                "rsi_filter": rsi_filter,
                "atr_expansion": atr_expansion,
            },
        )

        if core_signal_ready:
            filter_result = run_trade_filters(
                entry_candles,
                trend_ema50=float(trend_last["ema50"]),
                trend_ema200=float(trend_last["ema200"]),
                trend_atr=float(trend_last["atr14"]),
            )
            if not filter_result["allowed"]:
                return self._build_filter_skip_decision(
                    candle_time=candle_time,
                    strategy="xau_4_rule_breakout_strategy",
                    session=session,
                    score=score,
                    direction=direction,
                    trend_alignment=trend_alignment,
                    price_trigger=price_trigger,
                    rsi_filter=rsi_filter,
                    atr_expansion=atr_expansion,
                    session_filter=session_filter,
                    breakdown=breakdown,
                    filter_reason=str(filter_result["reason"]),
                    filter_details=filter_result,
                )

        signals: list[TradeSignal] = []
        primary_signal: TradeSignal | None = None
        if core_signal_ready:
            signals = self._build_output_signals(
                timestamp=candle_time,
                direction=direction,
                score=score,
                entry_price=float(entry_last["close"]),
                atr_value=float(entry_last["atr14"]),
                session=session,
            )
            if signals:
                primary_signal = signals[0]

        decision = SignalDecision(
            candle_time_utc=candle_time,
            symbol=self._normalize_symbol(self.symbol),
            strategy="xau_4_rule_breakout_strategy",
            session=session,
            score=score,
            score_threshold=self.threshold,
            direction=direction,
            trend_alignment=trend_alignment,
            price_trigger=price_trigger,
            rsi_filter=rsi_filter,
            atr_expansion=atr_expansion,
            session_filter=session_filter,
            signal_generated=bool(signals),
            reason=reason,
            breakdown=breakdown,
            signal=primary_signal,
            signals=signals,
        )
        self._log_decision(decision)
        return decision

    def _build_output_signals(
        self,
        *,
        timestamp: pd.Timestamp,
        direction: str,
        score: int,
        entry_price: float,
        atr_value: float,
        session: str,
    ) -> list[TradeSignal]:
        normalized_modes = []
        for mode in self.signal_modes:
            normalized_mode = str(mode).strip().lower()
            if normalized_mode in {"binary", "forex"} and normalized_mode not in normalized_modes:
                normalized_modes.append(normalized_mode)

        signals: list[TradeSignal] = []
        for mode in normalized_modes:
            if mode == "binary":
                signals.append(
                    TradeSignal(
                        timestamp_utc=timestamp,
                        symbol=self._normalize_symbol(self.symbol),
                        direction=direction,
                        score=score,
                        score_threshold=self.threshold,
                        entry_price=entry_price,
                        stop_loss=None,
                        take_profit=None,
                        signal_kind="binary",
                        trend_timeframe="15m",
                        entry_timeframe="5m",
                        atr=atr_value,
                        expiry_minutes=5,
                        reason_summary="15m EMA trend + 5m breakout + RSI + ATR",
                        session=session,
                    )
                )
                continue

            stop_loss, take_profit = self._forex_targets(direction, entry_price, atr_value)
            signals.append(
                TradeSignal(
                    timestamp_utc=timestamp,
                    symbol=self._normalize_symbol(self.symbol),
                    direction=direction,
                    score=score,
                    score_threshold=self.threshold,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    signal_kind="forex",
                    trend_timeframe="15m",
                    entry_timeframe="5m",
                    atr=atr_value,
                    expiry_minutes=None,
                    reason_summary="15m EMA trend + 5m breakout + RSI + ATR",
                    session=session,
                )
            )

        return signals

    def _log_decision(self, decision: SignalDecision) -> None:
        signal_name = decision.direction if decision.signal_generated else None
        self.decision_logger.log_result(signal_name, reason=decision.reason)
        self.decision_logger.log_decision(
            {
                "timestamp": decision.candle_time_utc,
                "symbol": decision.symbol,
                "strategy": decision.strategy,
                "session": decision.session,
                "trend_alignment": decision.trend_alignment,
                "price_trigger": decision.price_trigger,
                "rsi_filter": decision.rsi_filter,
                "atr_expansion": decision.atr_expansion,
                "session_filter": decision.session_filter,
                "signal_score": decision.score,
                "score_threshold": decision.score_threshold,
                "signal_generated": decision.signal_generated,
                "reason": decision.reason,
                "direction": decision.direction,
                "breakdown": decision.breakdown,
                "signal_modes": [signal.signal_kind for signal in decision.signals],
            }
        )

    def _build_filter_skip_decision(
        self,
        *,
        candle_time: pd.Timestamp,
        strategy: str,
        session: str,
        score: int,
        direction: str,
        trend_alignment: bool,
        price_trigger: bool,
        rsi_filter: bool,
        atr_expansion: bool,
        session_filter: bool,
        breakdown: dict[str, int],
        filter_reason: str,
        filter_details: dict[str, Any] | None = None,
    ) -> SignalDecision:
        reason = f"skipped:{filter_reason}"
        decision = SignalDecision(
            candle_time_utc=candle_time,
            symbol=self._normalize_symbol(self.symbol),
            strategy=strategy,
            session=session,
            score=score,
            score_threshold=self.threshold,
            direction=direction,
            trend_alignment=trend_alignment,
            price_trigger=price_trigger,
            rsi_filter=rsi_filter,
            atr_expansion=atr_expansion,
            session_filter=session_filter,
            signal_generated=False,
            reason=reason,
            breakdown=breakdown,
            signal=None,
            signals=[],
        )
        self.decision_logger.log_filter(decision.symbol, filter_reason, filter_details)
        self.decision_logger.log_skip(
            decision.symbol,
            filter_reason,
            {
                "timestamp": decision.candle_time_utc,
                "strategy": decision.strategy,
                "session": decision.session,
                "trend_alignment": decision.trend_alignment,
                "price_trigger": decision.price_trigger,
                "rsi_filter": decision.rsi_filter,
                "atr_expansion": decision.atr_expansion,
                "session_filter": decision.session_filter,
                "signal_score": decision.score,
                "score_threshold": decision.score_threshold,
                "direction": decision.direction,
                "breakdown": decision.breakdown,
                "signal_modes": list(self.signal_modes),
                "filter_details": filter_details or {},
            },
        )
        self.decision_logger.log_result(None, reason=reason)
        return decision

    def _score_breakdown(
        self,
        *,
        trend_alignment: bool,
        price_trigger: bool,
        rsi_filter: bool,
        atr_expansion: bool,
    ) -> dict[str, int]:
        return {
            "trend_alignment": self.rule_weight if trend_alignment else 0,
            "price_trigger": self.rule_weight if price_trigger else 0,
            "rsi_filter": self.rule_weight if rsi_filter else 0,
            "atr_expansion": self.rule_weight if atr_expansion else 0,
        }

    def _build_reason(self, *, direction: str, checks: dict[str, bool]) -> str:
        if direction == "NONE":
            return "rejected:no_trend_alignment"
        failed = [name for name, passed in checks.items() if not passed]
        if not failed:
            return "accepted"
        return f"rejected:{','.join(failed)}"

    @staticmethod
    def _ensure_minimum_rows(trend_candles: pd.DataFrame, entry_candles: pd.DataFrame) -> None:
        if len(trend_candles) < 220:
            raise ValueError("Need at least 220 trend candles for EMA200-based evaluation.")
        if len(entry_candles) < 220:
            raise ValueError("Need at least 220 entry candles for EMA200-based evaluation.")

    @staticmethod
    def _atr_expanding(row: pd.Series) -> bool:
        atr14 = float(row["atr14"])
        atr20_avg = float(row.get("atr20_avg", 0.0) or 0.0)
        return atr20_avg > 0 and atr14 > atr20_avg

    def _forex_targets(self, direction: str, entry_price: float, atr_value: float) -> tuple[float, float]:
        if direction == "BUY":
            stop_loss = entry_price - (atr_value * self.forex_sl_atr_multiplier)
            take_profit = entry_price + (atr_value * self.forex_tp_atr_multiplier)
        else:
            stop_loss = entry_price + (atr_value * self.forex_sl_atr_multiplier)
            take_profit = entry_price - (atr_value * self.forex_tp_atr_multiplier)
        return round(stop_loss, 2), round(take_profit, 2)

    @staticmethod
    def _normalize_symbol(value: str) -> str:
        return value.replace("/", "").replace(":", "").replace("-", "").replace(" ", "").upper()

    @staticmethod
    def _as_timestamp(value: Any) -> pd.Timestamp:
        if isinstance(value, pd.Timestamp):
            ts = value
        else:
            ts = pd.Timestamp(value)
        if ts.tzinfo is None:
            return ts.tz_localize(pytz.UTC)
        return ts.tz_convert(pytz.UTC)

    @staticmethod
    def _detect_session(now_utc: datetime.datetime) -> str:
        current_hour = now_utc.hour
        if 12 <= current_hour < 16:
            return "OVERLAP"
        if 7 <= current_hour < 16:
            return "LONDON"
        if 16 <= current_hour < 21:
            return "NEW_YORK"
        return "ASIAN"
