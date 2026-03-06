from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class ThresholdOptimizer:
    """
    Analyze the last N completed trades and recommend a score threshold.
    """

    lookback_trades: int = 100
    score_ranges: tuple[tuple[int, int | None], ...] = field(
        default_factory=lambda: ((60, 65), (65, 70), (70, 75), (75, 80), (80, None))
    )
    minimum_trades_per_threshold: int = 5

    def analyze(self, *, trades: pd.DataFrame, current_threshold: int) -> dict[str, Any]:
        if trades is None or trades.empty:
            return {
                "analyzed_trades": 0,
                "overall_win_rate": 0.0,
                "range_stats": [],
                "candidate_stats": [],
                "optimal_threshold": int(current_threshold),
            }

        frame = trades.tail(self.lookback_trades).copy()
        frame["score"] = pd.to_numeric(frame["score"], errors="coerce").fillna(0)
        frame["result"] = frame["result"].astype(str).str.upper()
        frame = frame[frame["result"].isin({"WIN", "LOSS"})].reset_index(drop=True)
        if frame.empty:
            return {
                "analyzed_trades": 0,
                "overall_win_rate": 0.0,
                "range_stats": [],
                "candidate_stats": [],
                "optimal_threshold": int(current_threshold),
            }

        range_stats = [self._range_stat(frame=frame, lower=lower, upper=upper) for lower, upper in self.score_ranges]
        thresholds = [lower for lower, _ in self.score_ranges]
        candidate_stats = [self._candidate_stat(frame=frame, threshold=threshold) for threshold in thresholds]
        overall_win_rate = self._win_rate(frame)
        optimal_threshold = self._choose_threshold(candidate_stats=candidate_stats, fallback=int(current_threshold))

        return {
            "analyzed_trades": int(len(frame)),
            "overall_win_rate": overall_win_rate,
            "range_stats": range_stats,
            "candidate_stats": candidate_stats,
            "optimal_threshold": int(optimal_threshold),
        }

    def _choose_threshold(self, *, candidate_stats: list[dict[str, Any]], fallback: int) -> int:
        eligible = [row for row in candidate_stats if int(row["total_trades"]) >= self.minimum_trades_per_threshold]
        if not eligible:
            return int(fallback)
        best = max(
            eligible,
            key=lambda row: (
                float(row["win_rate"]),
                int(row["threshold"]),
                int(row["total_trades"]),
            ),
        )
        return int(best["threshold"])

    def _range_stat(self, *, frame: pd.DataFrame, lower: int, upper: int | None) -> dict[str, Any]:
        if upper is None:
            subset = frame[frame["score"] >= lower]
            label = f"{lower}+"
        else:
            subset = frame[(frame["score"] >= lower) & (frame["score"] < upper)]
            label = f"{lower}-{upper}"
        return {
            "range": label,
            "total_trades": int(len(subset)),
            "win_rate": self._win_rate(subset),
        }

    def _candidate_stat(self, *, frame: pd.DataFrame, threshold: int) -> dict[str, Any]:
        subset = frame[frame["score"] >= threshold]
        return {
            "threshold": int(threshold),
            "total_trades": int(len(subset)),
            "win_rate": self._win_rate(subset),
        }

    @staticmethod
    def _win_rate(frame: pd.DataFrame) -> float:
        if frame.empty:
            return 0.0
        wins = int((frame["result"] == "WIN").sum())
        return round((wins / len(frame)) * 100.0, 2)
