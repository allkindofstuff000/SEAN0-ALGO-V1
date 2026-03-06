from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

import pandas as pd

from bot.backtest.data_loader import DataLoader
from bot.backtest.performance_analyzer import PerformanceAnalyzer
from bot.backtest.trade_simulator import TradeSimulator
from bot.config.config import (
    BACKTEST_BINARY_PAYOUT,
    BACKTEST_CSV_PATH,
    BACKTEST_END_DATE,
    BACKTEST_INITIAL_CAPITAL,
    BACKTEST_OUTPUT_DIR,
    BACKTEST_RISK_PERCENTAGE,
    BACKTEST_START_DATE,
    BACKTEST_SYMBOL,
    BACKTEST_TIMEFRAME,
    BINARY_EXPIRY,
    COOLDOWN_CANDLES,
    EXCHANGE,
    FOREX_MAX_HOLDING_MINUTES,
    FOREX_STOP_LOSS_ATR_MULTIPLIER,
    FOREX_TAKE_PROFIT_ATR_MULTIPLIER,
    LOSS_STREAK_LIMIT,
    MAX_SIGNALS_PER_DAY,
    MINIMUM_CANDLES,
    PRIMARY_TIMEFRAME,
    SESSION_END_UTC,
    SESSION_START_UTC,
    SIGNAL_MODE,
    SIGNAL_SCORE_THRESHOLD,
)
from bot.execution.signal_router import SignalRouter
from bot.indicators.indicator_engine import IndicatorEngine
from bot.market.liquidity_map import LiquidityMapEngine
from bot.market.regime_detector import RegimeDetector
from bot.market.session_engine import SessionEngine
from bot.risk.risk_manager import RiskManager
from bot.signals.signal_logic import SignalEvaluation, SignalGenerator


LOGGER = logging.getLogger(__name__)


class NullDecisionLogger:
    """Disable decision-trace writes during bulk backtests."""

    def log_decision(self, payload: dict[str, Any]) -> None:  # pragma: no cover - trivial no-op
        return


@dataclass
class BacktestConfig:
    symbol: str = BACKTEST_SYMBOL
    timeframe: str = BACKTEST_TIMEFRAME or PRIMARY_TIMEFRAME
    start_date: str | None = BACKTEST_START_DATE or None
    end_date: str | None = BACKTEST_END_DATE or None
    csv_path: str | Path | None = BACKTEST_CSV_PATH
    exchange_name: str = EXCHANGE
    mode: str = SIGNAL_MODE
    score_threshold: int = SIGNAL_SCORE_THRESHOLD
    minimum_candles: int = MINIMUM_CANDLES
    max_signals_per_day: int = MAX_SIGNALS_PER_DAY
    cooldown_candles: int = COOLDOWN_CANDLES
    max_loss_streak: int = LOSS_STREAK_LIMIT
    binary_expiry: str = BINARY_EXPIRY
    forex_stop_loss_atr_multiplier: float = FOREX_STOP_LOSS_ATR_MULTIPLIER
    forex_take_profit_atr_multiplier: float = FOREX_TAKE_PROFIT_ATR_MULTIPLIER
    forex_max_holding_minutes: int = FOREX_MAX_HOLDING_MINUTES
    initial_capital: float = BACKTEST_INITIAL_CAPITAL
    risk_per_trade_pct: float = BACKTEST_RISK_PERCENTAGE
    binary_payout: float = BACKTEST_BINARY_PAYOUT
    output_dir: Path = BACKTEST_OUTPUT_DIR


@dataclass
class BacktestResult:
    config: BacktestConfig
    trades: list[dict[str, Any]]
    metrics: dict[str, Any]
    candles_loaded: int
    processed_candles: int
    signals_generated: int
    signals_blocked: int
    evaluations: list[dict[str, Any]]
    runtime_seconds: float

    def summary(self) -> dict[str, Any]:
        payload = dict(self.metrics)
        payload.update(
            {
                "candles_loaded": self.candles_loaded,
                "processed_candles": self.processed_candles,
                "signals_generated": self.signals_generated,
                "signals_blocked": self.signals_blocked,
                "runtime_seconds": round(self.runtime_seconds, 3),
            }
        )
        return payload


@dataclass
class BacktestRunner:
    config: BacktestConfig
    data_loader: DataLoader = field(default_factory=DataLoader)
    indicator_engine: IndicatorEngine = field(default_factory=IndicatorEngine)
    liquidity_engine: LiquidityMapEngine = field(default_factory=LiquidityMapEngine)
    regime_detector: RegimeDetector = field(default_factory=RegimeDetector)
    session_engine: SessionEngine = field(default_factory=SessionEngine)
    performance_analyzer: PerformanceAnalyzer = field(default_factory=PerformanceAnalyzer)

    def run(
        self,
        *,
        data: pd.DataFrame | None = None,
        pre_enriched: bool = False,
        trade_start_time: datetime | None = None,
    ) -> BacktestResult:
        started = perf_counter()
        raw = (
            data.copy()
            if data is not None
            else self.data_loader.load(
                csv_path=self.config.csv_path,
                symbol=self.config.symbol,
                timeframe=self.config.timeframe,
                start_date=self.config.start_date,
                end_date=self.config.end_date,
            )
        )
        enriched = raw.copy() if pre_enriched else self.indicator_engine.add_indicators(raw)
        enriched = enriched.dropna(subset=["ema20", "ema50", "atr14", "macd", "macd_signal", "vwap"]).reset_index(drop=True)
        if len(enriched) < self.config.minimum_candles:
            raise ValueError(
                f"Backtest requires at least {self.config.minimum_candles} enriched candles; got {len(enriched)}."
            )

        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        risk_state_path = self.config.output_dir / f"risk_state.backtest.{uuid4().hex[:8]}.json"
        if risk_state_path.exists():
            risk_state_path.unlink()

        signal_generator = SignalGenerator(
            score_threshold=self.config.score_threshold,
            decision_logger=NullDecisionLogger(),
        )
        risk_manager = RiskManager(
            max_signals_per_day=self.config.max_signals_per_day,
            cooldown_candles=self.config.cooldown_candles,
            max_loss_streak=self.config.max_loss_streak,
            candle_minutes=self._timeframe_to_minutes(self.config.timeframe),
            session_start_utc=SESSION_START_UTC,
            session_end_utc=SESSION_END_UTC,
            state_path=risk_state_path,
        )
        signal_router = SignalRouter(
            mode=self.config.mode,
            binary_expiry=self.config.binary_expiry,
            forex_stop_loss_atr_multiplier=self.config.forex_stop_loss_atr_multiplier,
            forex_take_profit_atr_multiplier=self.config.forex_take_profit_atr_multiplier,
        )
        simulator = TradeSimulator(
            initial_capital=self.config.initial_capital,
            risk_per_trade_pct=self.config.risk_per_trade_pct,
            binary_payout=self.config.binary_payout,
            forex_max_holding_candles=max(
                1,
                int(round(self.config.forex_max_holding_minutes / self._timeframe_to_minutes(self.config.timeframe))),
            ),
        )

        warmup = max(
            self.config.minimum_candles,
            self.indicator_engine.ema_slow,
            self.liquidity_engine.lookback,
            self.regime_detector.compression_lookback + 2,
            30,
        )
        evaluations: list[dict[str, Any]] = []
        signals_generated = 0
        signals_blocked = 0

        for index in range(warmup - 1, len(enriched)):
            current_time = self._coerce_timestamp(enriched.iloc[index]["timestamp"])
            risk_manager.reset_daily_if_needed(current_time)

            settled = simulator.settle_pending_trades(df=enriched, current_index=index)
            for trade in settled:
                if trade["result"] == "WIN":
                    risk_manager.record_outcome(True)
                elif trade["result"] == "LOSS":
                    risk_manager.record_outcome(False)

            if trade_start_time is not None and current_time < trade_start_time:
                continue

            window = enriched.iloc[: index + 1].copy()
            liquidity_map = self.liquidity_engine.build(window)
            regime = self.regime_detector.detect(window)
            session = self.session_engine.detect(current_time)
            evaluation = signal_generator.evaluate(
                pair=self.config.symbol,
                timeframe=self.config.timeframe,
                df=window,
                liquidity_map=liquidity_map,
                regime=regime,
                session=session,
            )
            evaluation = self._align_evaluation_timestamp(evaluation=evaluation, candle_time=current_time)
            evaluations.append(self._evaluation_record(evaluation=evaluation))

            if not evaluation.signal_generated or evaluation.signal is None:
                continue

            allowed, block_reason = risk_manager.can_emit_signal(current_time)
            if not allowed:
                signals_blocked += 1
                evaluations[-1]["blocked_reason"] = block_reason
                continue

            routed_signal = signal_router.route(evaluation.signal)
            simulator.open_trade(
                signal=evaluation.signal,
                routed_signal=routed_signal,
                timeframe=self.config.timeframe,
                entry_index=index,
                entry_timestamp=current_time,
            )
            risk_manager.record_signal(current_time)
            signals_generated += 1

        forced = simulator.force_close_open_trades(df=enriched)
        for trade in forced:
            if trade["result"] == "WIN":
                risk_manager.record_outcome(True)
            elif trade["result"] == "LOSS":
                risk_manager.record_outcome(False)

        metrics = self.performance_analyzer.analyze(
            trades=simulator.closed_trades,
            initial_capital=self.config.initial_capital,
        )
        runtime_seconds = perf_counter() - started
        if risk_state_path.exists():
            risk_state_path.unlink()
        LOGGER.info(
            "backtest_completed symbol=%s timeframe=%s candles=%s trades=%s win_rate=%.2f runtime=%.2fs",
            self.config.symbol,
            self.config.timeframe,
            len(enriched),
            metrics["total_trades"],
            metrics["win_rate"],
            runtime_seconds,
        )
        return BacktestResult(
            config=self.config,
            trades=simulator.closed_trades,
            metrics=metrics,
            candles_loaded=len(enriched),
            processed_candles=max(0, len(enriched) - (warmup - 1)),
            signals_generated=signals_generated,
            signals_blocked=signals_blocked,
            evaluations=evaluations,
            runtime_seconds=runtime_seconds,
        )

    @staticmethod
    def _align_evaluation_timestamp(*, evaluation: SignalEvaluation, candle_time: datetime) -> SignalEvaluation:
        evaluation.timestamp_utc = candle_time
        if evaluation.signal is not None:
            evaluation.signal.timestamp_utc = candle_time
        return evaluation

    @staticmethod
    def _evaluation_record(*, evaluation: SignalEvaluation) -> dict[str, Any]:
        return {
            "timestamp": evaluation.timestamp_utc.isoformat(),
            "pair": evaluation.pair,
            "timeframe": evaluation.timeframe,
            "price": round(float(evaluation.price), 6),
            "direction": evaluation.direction,
            "score": int(evaluation.score),
            "score_threshold": int(evaluation.score_threshold),
            "regime": evaluation.regime,
            "session": evaluation.session,
            "signal_generated": bool(evaluation.signal_generated),
            "reason": evaluation.reason,
            "breakdown": dict(evaluation.breakdown),
        }

    @staticmethod
    def _coerce_timestamp(value: Any) -> datetime:
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize("UTC")
        else:
            timestamp = timestamp.tz_convert("UTC")
        return timestamp.to_pydatetime()

    @staticmethod
    def _timeframe_to_minutes(raw: str) -> int:
        value = str(raw).strip().lower()
        if value.endswith("m"):
            return int(value[:-1])
        if value.endswith("h"):
            return int(value[:-1]) * 60
        if value.endswith("d"):
            return int(value[:-1]) * 1440
        raise ValueError(f"Unsupported timeframe format: {raw}")
