from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any

import numpy as np
import pandas as pd

from bot.backtest.backtest_runner import BacktestConfig, BacktestRunner


@dataclass(frozen=True)
class ParameterSet:
    """
    Candidate parameter bundle evaluated during walk-forward optimization.
    """

    signal_score_threshold: int
    atr_multiplier: float
    cooldown_candles: int
    max_signals_per_day: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_score_threshold": int(self.signal_score_threshold),
            "atr_multiplier": round(float(self.atr_multiplier), 4),
            "cooldown_candles": int(self.cooldown_candles),
            "max_signals_per_day": int(self.max_signals_per_day),
        }


@dataclass
class OptimizationResult:
    best_parameters: ParameterSet
    best_training_performance: dict[str, Any]
    candidate_results: list[dict[str, Any]]
    objective: str


@dataclass
class ParameterOptimizer:
    """
    Grid-search optimizer that reuses the existing backtest engine.
    """

    threshold_bounds: tuple[int, int] = (60, 85)
    threshold_step: int = 5
    atr_bounds: tuple[float, float] = (1.0, 2.5)
    atr_step: float = 0.5
    cooldown_values: tuple[int, ...] = (2, 4, 6)
    max_signals_values: tuple[int, ...] = (2, 3, 4)
    minimum_trades: int = 3
    objective: str = "profit_factor"

    def optimize(
        self,
        *,
        training_data: pd.DataFrame,
        base_config: BacktestConfig,
        pre_enriched: bool = False,
    ) -> OptimizationResult:
        candidate_results: list[dict[str, Any]] = []

        for candidate in self._parameter_grid(base_config=base_config):
            config = self.apply_parameters(base_config=base_config, params=candidate)
            result = BacktestRunner(config=config).run(data=training_data, pre_enriched=pre_enriched)
            metrics = result.metrics
            candidate_results.append(
                {
                    "parameters": candidate.to_dict(),
                    "metrics": metrics,
                    "objective_score": self._objective_score(metrics),
                }
            )

        if not candidate_results:
            raise ValueError("Parameter optimizer produced no candidates.")

        best = max(candidate_results, key=lambda item: item["objective_score"])
        return OptimizationResult(
            best_parameters=ParameterSet(**best["parameters"]),
            best_training_performance=best["metrics"],
            candidate_results=candidate_results,
            objective=self.objective,
        )

    def apply_parameters(self, *, base_config: BacktestConfig, params: ParameterSet) -> BacktestConfig:
        reward_ratio = 1.0
        if float(base_config.forex_stop_loss_atr_multiplier) > 0:
            reward_ratio = float(base_config.forex_take_profit_atr_multiplier) / float(
                base_config.forex_stop_loss_atr_multiplier
            )
        return replace(
            base_config,
            score_threshold=int(params.signal_score_threshold),
            cooldown_candles=int(params.cooldown_candles),
            max_signals_per_day=int(params.max_signals_per_day),
            forex_stop_loss_atr_multiplier=float(params.atr_multiplier),
            forex_take_profit_atr_multiplier=round(float(params.atr_multiplier) * reward_ratio, 4),
        )

    def _parameter_grid(self, *, base_config: BacktestConfig) -> list[ParameterSet]:
        thresholds = range(
            int(self.threshold_bounds[0]),
            int(self.threshold_bounds[1]) + int(self.threshold_step),
            int(self.threshold_step),
        )
        atr_values = self._float_range(
            lower=float(self.atr_bounds[0]),
            upper=float(self.atr_bounds[1]),
            step=float(self.atr_step),
        )

        return [
            ParameterSet(
                signal_score_threshold=int(threshold),
                atr_multiplier=float(atr_multiplier),
                cooldown_candles=int(cooldown),
                max_signals_per_day=int(max_signals),
            )
            for threshold in thresholds
            for atr_multiplier in atr_values
            for cooldown in self.cooldown_values
            for max_signals in self.max_signals_values
        ]

    def _objective_score(self, metrics: dict[str, Any]) -> tuple[Any, ...]:
        total_trades = int(metrics.get("total_trades", 0))
        win_rate = float(metrics.get("win_rate", 0.0))
        net_profit = float(metrics.get("net_profit", 0.0))
        profit_factor = float(metrics.get("profit_factor", 0.0))
        if math.isinf(profit_factor):
            profit_factor = 10.0
        max_drawdown = float(metrics.get("max_drawdown", 0.0))
        meets_minimum = 1 if total_trades >= int(self.minimum_trades) else 0

        if self.objective == "win_rate":
            return (meets_minimum, win_rate, profit_factor, net_profit, -max_drawdown, total_trades)
        return (meets_minimum, profit_factor, win_rate, net_profit, -max_drawdown, total_trades)

    @staticmethod
    def _float_range(*, lower: float, upper: float, step: float) -> list[float]:
        values = np.arange(lower, upper + (step / 2.0), step)
        rounded = [round(float(value), 4) for value in values]
        deduped: list[float] = []
        for value in rounded:
            if not deduped or value != deduped[-1]:
                deduped.append(value)
        return deduped
