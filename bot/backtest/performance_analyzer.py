from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class PerformanceAnalyzer:
    """
    Calculate portfolio and trade quality metrics from settled backtest trades.
    """

    def analyze(self, *, trades: list[dict[str, Any]], initial_capital: float) -> dict[str, Any]:
        if not trades:
            return {
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "breakeven": 0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "max_drawdown": 0.0,
                "average_score": 0.0,
                "average_pnl": 0.0,
                "average_duration_candles": 0.0,
                "net_profit": 0.0,
                "ending_equity": float(initial_capital),
                "signals_per_session": {},
                "session_win_rate": {},
                "score_distribution": {},
            }

        frame = pd.DataFrame(trades).copy()
        frame["pnl"] = pd.to_numeric(frame.get("pnl"), errors="coerce").fillna(0.0)
        frame["score"] = pd.to_numeric(frame.get("score"), errors="coerce").fillna(0.0)
        frame["duration_candles"] = pd.to_numeric(frame.get("duration_candles"), errors="coerce").fillna(0.0)
        frame["equity_after"] = pd.to_numeric(frame.get("equity_after"), errors="coerce").ffill().fillna(initial_capital)
        frame["result"] = frame["result"].astype(str).str.upper()

        wins = int((frame["result"] == "WIN").sum())
        losses = int((frame["result"] == "LOSS").sum())
        breakeven = int((frame["result"] == "BREAKEVEN").sum())
        total = int(len(frame))

        gross_profit = float(frame.loc[frame["pnl"] > 0, "pnl"].sum())
        gross_loss = float(frame.loc[frame["pnl"] < 0, "pnl"].sum())
        profit_factor = gross_profit / abs(gross_loss) if gross_loss < 0 else float("inf") if gross_profit > 0 else 0.0
        max_drawdown = self._max_drawdown(frame["equity_after"])

        signals_per_session = frame.groupby("session").size().to_dict()
        session_win_rate = self._session_win_rate(frame)

        bins = [float("-inf"), 60, 65, 70, 75, 80, float("inf")]
        labels = ["<60", "60-65", "65-70", "70-75", "75-80", "80+"]
        score_buckets = pd.cut(frame["score"], bins=bins, labels=labels, include_lowest=True, right=False)
        score_distribution = score_buckets.value_counts().reindex(labels, fill_value=0).to_dict()

        return {
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "breakeven": breakeven,
            "win_rate": round((wins / total) * 100.0, 2) if total else 0.0,
            "profit_factor": round(float(profit_factor), 4) if np.isfinite(profit_factor) else float("inf"),
            "max_drawdown": round(float(max_drawdown), 2),
            "average_score": round(float(frame["score"].mean()), 2),
            "average_pnl": round(float(frame["pnl"].mean()), 4),
            "average_duration_candles": round(float(frame["duration_candles"].mean()), 2),
            "net_profit": round(float(frame["pnl"].sum()), 4),
            "ending_equity": round(float(frame["equity_after"].iloc[-1]), 4),
            "signals_per_session": {str(key): int(value) for key, value in signals_per_session.items()},
            "session_win_rate": session_win_rate,
            "score_distribution": {str(key): int(value) for key, value in score_distribution.items()},
        }

    @staticmethod
    def _max_drawdown(equity_series: pd.Series) -> float:
        if equity_series.empty:
            return 0.0
        running_max = equity_series.cummax()
        drawdown = (running_max - equity_series) / running_max.replace(0, np.nan)
        return float(drawdown.fillna(0.0).max() * 100.0)

    @staticmethod
    def _session_win_rate(frame: pd.DataFrame) -> dict[str, float]:
        metrics: dict[str, float] = {}
        for session, group in frame.groupby("session"):
            wins = int((group["result"] == "WIN").sum())
            total = int(len(group))
            metrics[str(session)] = round((wins / total) * 100.0, 2) if total else 0.0
        return metrics
