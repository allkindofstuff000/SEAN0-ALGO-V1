from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import matplotlib
import numpy as np
import pandas as pd

from bot.backtest.performance_analyzer import PerformanceAnalyzer

matplotlib.use("Agg")
import matplotlib.pyplot as plt

if TYPE_CHECKING:
    from bot.wfo.wfo_runner import WalkForwardResult


@dataclass
class WFOReport:
    """
    Generate summary artifacts and charts for walk-forward optimization runs.
    """

    output_dir: Path
    analyzer: PerformanceAnalyzer = field(default_factory=PerformanceAnalyzer)

    def generate(self, *, result: WalkForwardResult) -> dict[str, Path]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        artifacts: dict[str, Path] = {}

        trades = result.testing_trades()
        trades_frame = pd.DataFrame(trades)

        trade_log_path = self.output_dir / "wfo_trade_log.csv"
        trades_frame.to_csv(trade_log_path, index=False)
        artifacts["wfo_trade_log"] = trade_log_path

        summary = self._build_summary(result=result, trades=trades)
        summary_path = self.output_dir / "wfo_summary.json"
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        artifacts["wfo_summary"] = summary_path

        if result.window_results:
            artifacts["equity_curve"] = self._plot_equity_curve(result=result)
            artifacts["parameter_stability"] = self._plot_parameter_stability(result=result)
            artifacts["performance_per_window"] = self._plot_performance_per_window(result=result)

        return artifacts

    def _build_summary(self, *, result: WalkForwardResult, trades: list[dict[str, Any]]) -> dict[str, Any]:
        overall_trade_metrics = self.analyzer.analyze(
            trades=trades,
            initial_capital=result.config.initial_capital,
        )
        window_metrics = [window.testing_performance for window in result.window_results]
        window_profit_factors = [
            float(metrics.get("profit_factor", 0.0))
            for metrics in window_metrics
            if np.isfinite(float(metrics.get("profit_factor", 0.0)))
        ]
        average_profit_factor = round(float(np.mean(window_profit_factors)), 4) if window_profit_factors else 0.0

        window_equity = self._window_equity_curve(result=result)
        max_drawdown = self._max_drawdown(pd.Series([point["equity"] for point in window_equity])) if window_equity else 0.0

        return {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "config": self._serialize_config(result.config),
            "overview": {
                "windows_tested": len(result.window_results),
                "windows_skipped": len(result.skipped_windows),
                "candles_loaded": int(result.candles_loaded),
                "total_trades": int(overall_trade_metrics.get("total_trades", 0)),
                "overall_win_rate": float(overall_trade_metrics.get("win_rate", 0.0)),
                "average_profit_factor": average_profit_factor,
                "max_drawdown": round(float(max_drawdown), 2),
            },
            "overall_trade_metrics": overall_trade_metrics,
            "parameter_stability": self._parameter_stability(result=result),
            "performance_per_window": [window.summary() for window in result.window_results],
            "skipped_windows": result.skipped_windows,
        }

    def _plot_equity_curve(self, *, result: WalkForwardResult) -> Path:
        path = self.output_dir / "wfo_equity_curve.png"
        points = self._window_equity_curve(result=result)

        plt.style.use("dark_background")
        fig, axis = plt.subplots(figsize=(12, 6))
        axis.plot(
            [point["window_id"] for point in points],
            [point["equity"] for point in points],
            color="#4af2e3",
            linewidth=2.2,
            marker="o",
        )
        axis.set_title("Walk-Forward Equity Curve")
        axis.set_xlabel("Window")
        axis.set_ylabel("Equity")
        axis.grid(alpha=0.18)
        fig.tight_layout()
        fig.savefig(path, dpi=160)
        plt.close(fig)
        return path

    def _plot_parameter_stability(self, *, result: WalkForwardResult) -> Path:
        path = self.output_dir / "wfo_parameter_stability.png"
        windows = [window.window_id for window in result.window_results]
        thresholds = [window.selected_parameters["signal_score_threshold"] for window in result.window_results]
        atr_values = [window.selected_parameters["atr_multiplier"] for window in result.window_results]
        cooldowns = [window.selected_parameters["cooldown_candles"] for window in result.window_results]
        max_signals = [window.selected_parameters["max_signals_per_day"] for window in result.window_results]

        plt.style.use("dark_background")
        fig, axes = plt.subplots(2, 2, figsize=(14, 8), sharex=True)
        series = [
            (axes[0, 0], thresholds, "Threshold", "#4f8cff"),
            (axes[0, 1], atr_values, "ATR Multiplier", "#8f6bff"),
            (axes[1, 0], cooldowns, "Cooldown Candles", "#ffbf5e"),
            (axes[1, 1], max_signals, "Max Signals / Day", "#2fd38f"),
        ]
        for axis, values, title, color in series:
            axis.plot(windows, values, marker="o", linewidth=2.0, color=color)
            axis.set_title(title)
            axis.grid(alpha=0.18)

        fig.tight_layout()
        fig.savefig(path, dpi=160)
        plt.close(fig)
        return path

    def _plot_performance_per_window(self, *, result: WalkForwardResult) -> Path:
        path = self.output_dir / "wfo_performance_per_window.png"
        windows = [window.window_id for window in result.window_results]
        win_rates = [float(window.testing_performance.get("win_rate", 0.0)) for window in result.window_results]
        profit_factors = [
            float(window.testing_performance.get("profit_factor", 0.0))
            if np.isfinite(float(window.testing_performance.get("profit_factor", 0.0)))
            else 10.0
            for window in result.window_results
        ]

        plt.style.use("dark_background")
        fig, axis_left = plt.subplots(figsize=(14, 6))
        axis_left.bar(windows, win_rates, color="#4f8cff", alpha=0.75)
        axis_left.set_ylabel("Win Rate (%)")
        axis_left.set_xlabel("Window")
        axis_left.set_title("WFO Performance Per Window")
        axis_left.grid(axis="y", alpha=0.18)

        axis_right = axis_left.twinx()
        axis_right.plot(windows, profit_factors, color="#4af2e3", marker="o", linewidth=2.0)
        axis_right.set_ylabel("Profit Factor")

        fig.tight_layout()
        fig.savefig(path, dpi=160)
        plt.close(fig)
        return path

    def _window_equity_curve(self, *, result: WalkForwardResult) -> list[dict[str, Any]]:
        equity = float(result.config.initial_capital)
        points: list[dict[str, Any]] = []

        for window in result.window_results:
            ending_equity = float(window.testing_performance.get("ending_equity", result.config.initial_capital))
            window_return = (ending_equity - float(result.config.initial_capital)) / float(result.config.initial_capital)
            equity *= 1.0 + window_return
            points.append({"window_id": window.window_id, "equity": round(equity, 4)})

        return points

    def _parameter_stability(self, *, result: WalkForwardResult) -> dict[str, Any]:
        keys = [
            "signal_score_threshold",
            "atr_multiplier",
            "cooldown_candles",
            "max_signals_per_day",
        ]
        summary: dict[str, Any] = {}

        for key in keys:
            values = [float(window.selected_parameters[key]) for window in result.window_results]
            summary[key] = {
                "values": values,
                "unique_values": sorted(set(values)),
                "mean": round(float(np.mean(values)), 4) if values else 0.0,
                "std": round(float(np.std(values)), 4) if values else 0.0,
            }

        return summary

    @staticmethod
    def _max_drawdown(equity_series: pd.Series) -> float:
        if equity_series.empty:
            return 0.0
        running_max = equity_series.cummax()
        drawdown = (running_max - equity_series) / running_max.replace(0, np.nan)
        return float(drawdown.fillna(0.0).max() * 100.0)

    @staticmethod
    def _serialize_config(config: Any) -> dict[str, Any]:
        payload = asdict(config)
        return {key: str(value) if isinstance(value, Path) else value for key, value in payload.items()}
