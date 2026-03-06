from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bot.backtest.backtest_runner import BacktestConfig
from bot.backtest.data_loader import DataLoader
from bot.config.config import (
    BACKTEST_BINARY_PAYOUT,
    BACKTEST_CSV_PATH,
    BACKTEST_END_DATE,
    BACKTEST_INITIAL_CAPITAL,
    BACKTEST_RISK_PERCENTAGE,
    BACKTEST_START_DATE,
    BACKTEST_SYMBOL,
    BACKTEST_TIMEFRAME,
    BINARY_EXPIRY,
    EXCHANGE,
    FOREX_MAX_HOLDING_MINUTES,
    FOREX_STOP_LOSS_ATR_MULTIPLIER,
    FOREX_TAKE_PROFIT_ATR_MULTIPLIER,
    MINIMUM_CANDLES,
    PARAM_ATR_RANGE,
    PARAM_ATR_STEP,
    PARAM_THRESHOLD_RANGE,
    PARAM_THRESHOLD_STEP,
    SIGNAL_MODE,
    SIGNAL_SCORE_THRESHOLD,
    WFO_OUTPUT_DIR,
    WFO_STEP_DAYS,
    WFO_TEST_DAYS,
    WFO_TRAINING_DAYS,
)
from bot.wfo.parameter_optimizer import ParameterOptimizer
from bot.wfo.wfo_engine import WalkForwardEngine, WalkForwardWindowResult
from bot.wfo.window_generator import RollingWindowGenerator


LOGGER = logging.getLogger(__name__)


@dataclass
class WalkForwardConfig:
    symbol: str = BACKTEST_SYMBOL
    timeframe: str = BACKTEST_TIMEFRAME
    start_date: str | None = BACKTEST_START_DATE
    end_date: str | None = BACKTEST_END_DATE
    csv_path: str | None = BACKTEST_CSV_PATH
    exchange_name: str = EXCHANGE
    mode: str = SIGNAL_MODE
    training_days: int = WFO_TRAINING_DAYS
    testing_days: int = WFO_TEST_DAYS
    step_days: int = WFO_STEP_DAYS
    threshold_bounds: tuple[int, int] = PARAM_THRESHOLD_RANGE
    threshold_step: int = PARAM_THRESHOLD_STEP
    atr_bounds: tuple[float, float] = PARAM_ATR_RANGE
    atr_step: float = PARAM_ATR_STEP
    cooldown_values: tuple[int, ...] = (2, 4, 6)
    max_signals_values: tuple[int, ...] = (2, 3, 4)
    minimum_trades_for_optimization: int = 3
    minimum_candles: int = MINIMUM_CANDLES
    initial_capital: float = BACKTEST_INITIAL_CAPITAL
    risk_per_trade_pct: float = BACKTEST_RISK_PERCENTAGE
    binary_payout: float = BACKTEST_BINARY_PAYOUT
    score_threshold: int = SIGNAL_SCORE_THRESHOLD
    binary_expiry: str = BINARY_EXPIRY
    forex_stop_loss_atr_multiplier: float = FOREX_STOP_LOSS_ATR_MULTIPLIER
    forex_take_profit_atr_multiplier: float = FOREX_TAKE_PROFIT_ATR_MULTIPLIER
    forex_max_holding_minutes: int = FOREX_MAX_HOLDING_MINUTES
    output_dir: Path = WFO_OUTPUT_DIR

    def to_backtest_config(self) -> BacktestConfig:
        return BacktestConfig(
            symbol=self.symbol,
            timeframe=self.timeframe,
            start_date=self.start_date,
            end_date=self.end_date,
            csv_path=self.csv_path,
            exchange_name=self.exchange_name,
            mode=self.mode,
            score_threshold=self.score_threshold,
            minimum_candles=self.minimum_candles,
            binary_expiry=self.binary_expiry,
            max_signals_per_day=max(self.max_signals_values),
            cooldown_candles=min(self.cooldown_values),
            max_loss_streak=2,
            forex_stop_loss_atr_multiplier=self.forex_stop_loss_atr_multiplier,
            forex_take_profit_atr_multiplier=self.forex_take_profit_atr_multiplier,
            forex_max_holding_minutes=self.forex_max_holding_minutes,
            initial_capital=self.initial_capital,
            risk_per_trade_pct=self.risk_per_trade_pct,
            binary_payout=self.binary_payout,
            output_dir=self.output_dir / "window_runs",
        )


@dataclass
class WalkForwardResult:
    config: WalkForwardConfig
    window_results: list[WalkForwardWindowResult]
    candles_loaded: int
    skipped_windows: list[dict[str, Any]] = field(default_factory=list)

    def testing_trades(self) -> list[dict[str, Any]]:
        trades: list[dict[str, Any]] = []
        for window in self.window_results:
            trades.extend(window.testing_trades)
        return trades

    def summaries(self) -> list[dict[str, Any]]:
        return [window.summary() for window in self.window_results]


@dataclass
class WalkForwardRunner:
    config: WalkForwardConfig
    data_loader: DataLoader = field(default_factory=DataLoader)

    def run(self) -> WalkForwardResult:
        self.data_loader.exchange_name = self.config.exchange_name
        raw = self.data_loader.load(
            csv_path=self.config.csv_path,
            symbol=self.config.symbol,
            timeframe=self.config.timeframe,
            start_date=self.config.start_date,
            end_date=self.config.end_date,
        )
        generator = RollingWindowGenerator(
            training_days=self.config.training_days,
            testing_days=self.config.testing_days,
            step_days=self.config.step_days,
        )
        windows = generator.generate(raw)
        if not windows:
            raise ValueError("No walk-forward windows could be generated from the provided data.")

        optimizer = ParameterOptimizer(
            threshold_bounds=self.config.threshold_bounds,
            threshold_step=self.config.threshold_step,
            atr_bounds=self.config.atr_bounds,
            atr_step=self.config.atr_step,
            cooldown_values=self.config.cooldown_values,
            max_signals_values=self.config.max_signals_values,
            minimum_trades=self.config.minimum_trades_for_optimization,
        )
        base_backtest_config = self.config.to_backtest_config()
        engine = WalkForwardEngine(
            optimizer=optimizer,
            context_candles=max(int(base_backtest_config.minimum_candles) + 1, 301),
        )

        results: list[WalkForwardWindowResult] = []
        skipped: list[dict[str, Any]] = []

        for window in windows:
            try:
                result = engine.evaluate_window(window=window, base_config=base_backtest_config)
                results.append(result)
            except Exception as exc:
                LOGGER.exception("wfo_window_failed window_id=%s", window.window_id)
                skipped.append(
                    {
                        "window_id": window.window_id,
                        "train_start": window.train_start.isoformat(),
                        "train_end": window.train_end.isoformat(),
                        "test_start": window.test_start.isoformat(),
                        "test_end": window.test_end.isoformat(),
                        "error": str(exc),
                    }
                )

        if not results:
            raise ValueError("All walk-forward windows failed.")

        return WalkForwardResult(
            config=self.config,
            window_results=results,
            candles_loaded=len(raw),
            skipped_windows=skipped,
        )
