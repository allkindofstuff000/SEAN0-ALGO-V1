from __future__ import annotations

import csv
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import bot.config.config as config_module
from storage import read_json_with_fallback, write_json_locked

from bot.learning.performance_tracker import PerformanceTracker
from bot.learning.threshold_optimizer import ThresholdOptimizer


LOGGER = logging.getLogger(__name__)


@dataclass
class StrategyAdapter:
    """
    Maintain a dynamic score threshold driven by completed-trade performance.
    """

    state_path: Path
    history_path: Path
    default_threshold: int = 70
    min_threshold: int = 60
    max_threshold: int = 85
    optimization_frequency: int = 100
    adjustment_step: int = 5
    optimizer: ThresholdOptimizer = field(default_factory=ThresholdOptimizer)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)
    _state: dict[str, Any] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_history_file()
        self._state = self._load_state()
        self._sync_config_threshold(int(self._state["current_threshold"]))

    def get_threshold(self) -> int:
        with self._lock:
            self._refresh_state_from_disk()
            return int(self._state.get("current_threshold", self.default_threshold))

    def is_enabled(self) -> bool:
        with self._lock:
            self._refresh_state_from_disk()
            return bool(self._state.get("enabled", True))

    def last_update_utc(self) -> str | None:
        with self._lock:
            self._refresh_state_from_disk()
            return self._state.get("updated_at_utc")

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        with self._lock:
            self._refresh_state_from_disk()
            self._state["enabled"] = bool(enabled)
            self._state["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
            self._state["last_reason"] = "learning_enabled" if enabled else "learning_disabled"
            write_json_locked(self.state_path, self._state)
            LOGGER.info("learning_toggle enabled=%s", enabled)
            return dict(self._state)

    def reset(self) -> dict[str, Any]:
        with self._lock:
            self._refresh_state_from_disk()
            previous_threshold = int(self._state.get("current_threshold", self.default_threshold))
            self._state.update(
                {
                    "enabled": True,
                    "current_threshold": int(self.default_threshold),
                    "last_optimized_trade_count": 0,
                    "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                    "last_reason": "learning_reset",
                    "last_overall_win_rate": None,
                }
            )
            write_json_locked(self.state_path, self._state)
            self._sync_config_threshold(int(self._state["current_threshold"]))
            self._append_history_row(
                previous_threshold=previous_threshold,
                new_threshold=int(self._state["current_threshold"]),
                reason="learning_reset",
                overall_win_rate=0.0,
                optimal_threshold=int(self._state["current_threshold"]),
                analyzed_trades=0,
            )
            LOGGER.info("learning_reset threshold=%s", self._state["current_threshold"])
            return dict(self._state)

    def set_threshold(
        self,
        threshold: int,
        *,
        reason: str = "manual_threshold_update",
        analyzed_trades: int = 0,
        overall_win_rate: float | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._refresh_state_from_disk()
            previous_threshold = int(self._state.get("current_threshold", self.default_threshold))
            clamped_threshold = max(self.min_threshold, min(self.max_threshold, int(threshold)))
            self._state["current_threshold"] = clamped_threshold
            self._state["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
            self._state["last_reason"] = reason
            if overall_win_rate is not None:
                self._state["last_overall_win_rate"] = float(overall_win_rate)
            write_json_locked(self.state_path, self._state)
            self._sync_config_threshold(clamped_threshold)
            if clamped_threshold != previous_threshold:
                self._append_history_row(
                    previous_threshold=previous_threshold,
                    new_threshold=clamped_threshold,
                    reason=reason,
                    overall_win_rate=float(overall_win_rate or 0.0),
                    optimal_threshold=clamped_threshold,
                    analyzed_trades=int(analyzed_trades),
                )
            LOGGER.info(
                "threshold_set previous=%s new=%s reason=%s",
                previous_threshold,
                clamped_threshold,
                reason,
            )
            return dict(self._state)

    def maybe_optimize(self, tracker: PerformanceTracker) -> dict[str, Any] | None:
        with self._lock:
            self._refresh_state_from_disk()
            if not bool(self._state.get("enabled", True)):
                return None
            completed_trades = tracker.completed_trade_count()
            last_optimized = int(self._state.get("last_optimized_trade_count", 0))
            if completed_trades == 0 or (completed_trades - last_optimized) < int(self.optimization_frequency):
                return None

            current_threshold = int(self._state.get("current_threshold", self.default_threshold))
            analysis = self.optimizer.analyze(
                trades=tracker.load_recent_trades(limit=self.optimizer.lookback_trades),
                current_threshold=current_threshold,
            )
            optimized_threshold = int(analysis.get("optimal_threshold", current_threshold))
            overall_win_rate = float(analysis.get("overall_win_rate", 0.0))
            new_threshold, reason = self._derive_threshold(
                current_threshold=current_threshold,
                optimized_threshold=optimized_threshold,
                overall_win_rate=overall_win_rate,
            )

            previous_threshold = current_threshold
            self._state.update(
                {
                    "current_threshold": new_threshold,
                    "last_optimized_trade_count": completed_trades,
                    "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                    "last_reason": reason,
                    "last_overall_win_rate": overall_win_rate,
                }
            )
            write_json_locked(self.state_path, self._state)
            self._sync_config_threshold(new_threshold)

            if new_threshold != previous_threshold:
                self._append_history_row(
                    previous_threshold=previous_threshold,
                    new_threshold=new_threshold,
                    reason=reason,
                    overall_win_rate=overall_win_rate,
                    optimal_threshold=optimized_threshold,
                    analyzed_trades=int(analysis.get("analyzed_trades", 0)),
                )
                LOGGER.info(
                    "threshold_updated previous=%s new=%s reason=%s overall_win_rate=%.2f analyzed_trades=%s",
                    previous_threshold,
                    new_threshold,
                    reason,
                    overall_win_rate,
                    analysis.get("analyzed_trades", 0),
                )
            else:
                LOGGER.info(
                    "threshold_unchanged current=%s reason=%s overall_win_rate=%.2f analyzed_trades=%s",
                    new_threshold,
                    reason,
                    overall_win_rate,
                    analysis.get("analyzed_trades", 0),
                )

            result = dict(analysis)
            result.update(
                {
                    "updated": new_threshold != previous_threshold,
                    "previous_threshold": previous_threshold,
                    "new_threshold": new_threshold,
                    "reason": reason,
                    "enabled": True,
                }
            )
            return result

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._refresh_state_from_disk()
            return dict(self._state)

    def _derive_threshold(
        self,
        *,
        current_threshold: int,
        optimized_threshold: int,
        overall_win_rate: float,
    ) -> tuple[int, str]:
        if overall_win_rate < 55.0:
            proposed = max(current_threshold + self.adjustment_step, optimized_threshold)
            return min(self.max_threshold, proposed), "win_rate_below_55_increase_threshold"
        if overall_win_rate > 70.0:
            proposed = min(current_threshold - self.adjustment_step, optimized_threshold)
            return max(self.min_threshold, proposed), "win_rate_above_70_decrease_threshold"
        if optimized_threshold != current_threshold:
            clamped = max(self.min_threshold, min(self.max_threshold, optimized_threshold))
            return clamped, "optimizer_alignment"
        return current_threshold, "hold_threshold"

    def _load_state(self) -> dict[str, Any]:
        default_state = {
            "enabled": True,
            "current_threshold": int(self.default_threshold),
            "last_optimized_trade_count": 0,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "last_reason": "initial_threshold",
            "last_overall_win_rate": None,
        }
        if not self.state_path.exists():
            write_json_locked(self.state_path, default_state)
            self._append_history_row(
                previous_threshold=int(self.default_threshold),
                new_threshold=int(self.default_threshold),
                reason="initial_threshold",
                overall_win_rate=0.0,
                optimal_threshold=int(self.default_threshold),
                analyzed_trades=0,
            )
            return default_state
        return read_json_with_fallback(self.state_path, default_state)

    def _refresh_state_from_disk(self) -> None:
        self._state = self._load_state()

    def _sync_config_threshold(self, threshold: int) -> None:
        config_module.SIGNAL_SCORE_THRESHOLD = int(threshold)

    def _ensure_history_file(self) -> None:
        if self.history_path.exists():
            return
        with self.history_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=self._history_headers())
            writer.writeheader()

    def _append_history_row(
        self,
        *,
        previous_threshold: int,
        new_threshold: int,
        reason: str,
        overall_win_rate: float,
        optimal_threshold: int,
        analyzed_trades: int,
    ) -> None:
        with self.history_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=self._history_headers())
            writer.writerow(
                {
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "previous_threshold": int(previous_threshold),
                    "new_threshold": int(new_threshold),
                    "reason": reason,
                    "overall_win_rate": round(float(overall_win_rate), 2),
                    "optimal_threshold": int(optimal_threshold),
                    "analyzed_trades": int(analyzed_trades),
                }
            )

    @staticmethod
    def _history_headers() -> list[str]:
        return [
            "timestamp_utc",
            "previous_threshold",
            "new_threshold",
            "reason",
            "overall_win_rate",
            "optimal_threshold",
            "analyzed_trades",
        ]
