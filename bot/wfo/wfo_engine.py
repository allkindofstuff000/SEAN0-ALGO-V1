from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from bot.backtest.backtest_runner import BacktestConfig, BacktestRunner
from bot.indicators.indicator_engine import IndicatorEngine
from bot.wfo.parameter_optimizer import OptimizationResult, ParameterOptimizer
from bot.wfo.window_generator import WalkForwardWindow


@dataclass
class WalkForwardWindowResult:
    """
    Training optimization result and unseen testing performance for one WFO window.
    """

    window_id: str
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    selected_parameters: dict[str, Any]
    training_performance: dict[str, Any]
    testing_performance: dict[str, Any]
    candidate_count: int
    testing_trades: list[dict[str, Any]]

    def summary(self) -> dict[str, Any]:
        return {
            "window_id": self.window_id,
            "train_start": self.train_start,
            "train_end": self.train_end,
            "test_start": self.test_start,
            "test_end": self.test_end,
            "selected_parameters": self.selected_parameters,
            "training_performance": self.training_performance,
            "testing_performance": self.testing_performance,
            "candidate_count": self.candidate_count,
        }


@dataclass
class WalkForwardEngine:
    """
    Optimize on a training window and validate on the subsequent unseen window.
    """

    optimizer: ParameterOptimizer
    indicator_engine: IndicatorEngine = field(default_factory=IndicatorEngine)
    context_candles: int = 301

    def evaluate_window(
        self,
        *,
        window: WalkForwardWindow,
        base_config: BacktestConfig,
    ) -> WalkForwardWindowResult:
        training_enriched = self.indicator_engine.add_indicators(window.training_data)
        optimization = self.optimizer.optimize(
            training_data=training_enriched,
            base_config=base_config,
            pre_enriched=True,
        )
        selected_config = self.optimizer.apply_parameters(
            base_config=base_config,
            params=optimization.best_parameters,
        )

        testing_frame = self._testing_frame(window=window)
        testing_enriched = self.indicator_engine.add_indicators(testing_frame)
        testing_result = BacktestRunner(config=selected_config).run(
            data=testing_enriched,
            pre_enriched=True,
            trade_start_time=window.test_start.to_pydatetime(),
        )

        testing_trades = [
            self._annotate_trade(
                trade=trade,
                window=window,
                parameters=optimization.best_parameters.to_dict(),
            )
            for trade in testing_result.trades
        ]

        return WalkForwardWindowResult(
            window_id=window.window_id,
            train_start=window.train_start.isoformat(),
            train_end=window.train_end.isoformat(),
            test_start=window.test_start.isoformat(),
            test_end=window.test_end.isoformat(),
            selected_parameters=optimization.best_parameters.to_dict(),
            training_performance=optimization.best_training_performance,
            testing_performance=testing_result.metrics,
            candidate_count=len(optimization.candidate_results),
            testing_trades=testing_trades,
        )

    def _testing_frame(self, *, window: WalkForwardWindow) -> pd.DataFrame:
        context = window.training_data.tail(int(self.context_candles))
        combined = pd.concat([context, window.testing_data], ignore_index=True)
        combined = combined.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True)
        return combined

    @staticmethod
    def _annotate_trade(
        *,
        trade: dict[str, Any],
        window: WalkForwardWindow,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        record = dict(trade)
        record.update(
            {
                "window_id": window.window_id,
                "train_start": window.train_start.isoformat(),
                "train_end": window.train_end.isoformat(),
                "test_start": window.test_start.isoformat(),
                "test_end": window.test_end.isoformat(),
                "selected_threshold": parameters["signal_score_threshold"],
                "selected_atr_multiplier": parameters["atr_multiplier"],
                "selected_cooldown_candles": parameters["cooldown_candles"],
                "selected_max_signals_per_day": parameters["max_signals_per_day"],
            }
        )
        return record
