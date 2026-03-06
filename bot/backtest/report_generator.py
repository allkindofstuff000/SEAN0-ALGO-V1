from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt

if TYPE_CHECKING:
    from bot.backtest.backtest_runner import BacktestResult


@dataclass
class ReportGenerator:
    """
    Persist backtest trades, metrics, and optional chart artifacts.
    """

    output_dir: Path

    def generate(self, *, result: BacktestResult) -> dict[str, Path]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        artifacts: dict[str, Path] = {}

        trades_frame = pd.DataFrame(result.trades)
        trade_log_path = self.output_dir / "trade_log.csv"
        trades_frame.to_csv(trade_log_path, index=False)
        artifacts["trade_log"] = trade_log_path

        summary_path = self.output_dir / "performance_summary.json"
        summary_payload = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "config": self._serialize_config(result.config),
            "summary": result.summary(),
            "metrics": result.metrics,
        }
        summary_path.write_text(json.dumps(summary_payload, indent=2), encoding="utf-8")
        artifacts["performance_summary"] = summary_path

        if not trades_frame.empty:
            artifacts["equity_curve"] = self._plot_equity_curve(trades_frame)
            artifacts["score_distribution"] = self._plot_score_distribution(trades_frame)
            artifacts["session_performance"] = self._plot_session_performance(trades_frame)

        return artifacts

    def _plot_equity_curve(self, trades_frame: pd.DataFrame) -> Path:
        path = self.output_dir / "equity_curve.png"
        frame = trades_frame.copy()
        frame["equity_after"] = pd.to_numeric(frame["equity_after"], errors="coerce")

        plt.style.use("dark_background")
        fig, axis = plt.subplots(figsize=(12, 6))
        axis.plot(range(1, len(frame) + 1), frame["equity_after"], color="#4af2e3", linewidth=2.2)
        axis.set_title("Equity Curve")
        axis.set_xlabel("Trade Number")
        axis.set_ylabel("Equity")
        axis.grid(alpha=0.18)
        fig.tight_layout()
        fig.savefig(path, dpi=160)
        plt.close(fig)
        return path

    def _plot_score_distribution(self, trades_frame: pd.DataFrame) -> Path:
        path = self.output_dir / "score_distribution.png"
        frame = trades_frame.copy()
        frame["score"] = pd.to_numeric(frame["score"], errors="coerce").fillna(0)

        plt.style.use("dark_background")
        fig, axis = plt.subplots(figsize=(12, 6))
        axis.hist(frame["score"], bins=[0, 60, 65, 70, 75, 80, 100], color="#4f8cff", edgecolor="#dbeafe")
        axis.set_title("Signal Score Distribution")
        axis.set_xlabel("Score")
        axis.set_ylabel("Trade Count")
        axis.grid(alpha=0.18)
        fig.tight_layout()
        fig.savefig(path, dpi=160)
        plt.close(fig)
        return path

    def _plot_session_performance(self, trades_frame: pd.DataFrame) -> Path:
        path = self.output_dir / "session_performance.png"
        frame = trades_frame.copy()
        frame["result"] = frame["result"].astype(str).str.upper()
        grouped = (
            frame.assign(is_win=frame["result"].eq("WIN").astype(float))
            .groupby("session")["is_win"]
            .mean()
            .mul(100.0)
        )

        plt.style.use("dark_background")
        fig, axis = plt.subplots(figsize=(12, 6))
        axis.bar(grouped.index.astype(str), grouped.values, color="#2fd38f")
        axis.set_title("Session Win Rate")
        axis.set_xlabel("Session")
        axis.set_ylabel("Win Rate (%)")
        axis.set_ylim(0, 100)
        axis.grid(axis="y", alpha=0.18)
        fig.tight_layout()
        fig.savefig(path, dpi=160)
        plt.close(fig)
        return path

    @staticmethod
    def _serialize_config(config: Any) -> dict[str, Any]:
        payload = asdict(config)
        return {key: str(value) if isinstance(value, Path) else value for key, value in payload.items()}
