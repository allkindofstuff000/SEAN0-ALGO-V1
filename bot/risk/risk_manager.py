from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


@dataclass
class RiskManager:
    """
    Enforce operational risk controls before routing a signal.
    """

    max_signals_per_day: int = 3
    cooldown_candles: int = 3
    max_loss_streak: int = 2
    candle_minutes: int = 15
    session_start_utc: str = "00:00"
    session_end_utc: str = "23:59"
    state_path: Path = Path("bot_runtime/risk_state.json")
    loss_streak_limit: int | None = None
    _state: dict[str, Any] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        if self.loss_streak_limit is not None:
            self.max_loss_streak = int(self.loss_streak_limit)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state = self._load_state()

    def can_emit_signal(self, now_utc: datetime) -> tuple[bool, str]:
        if not self._within_session(now_utc):
            return False, "blocked_session_filter"

        if int(self._state.get("loss_streak", 0)) >= int(self.max_loss_streak):
            return False, "blocked_max_loss_streak"

        day_key = now_utc.strftime("%Y-%m-%d")
        sent_today = int(self._state.get("daily_counts", {}).get(day_key, 0))
        if sent_today >= int(self.max_signals_per_day):
            return False, "blocked_max_signals_per_day"

        last_iso = self._state.get("last_signal_utc")
        if last_iso:
            last = datetime.fromisoformat(str(last_iso))
            cooldown_seconds = int(self.cooldown_candles) * int(self.candle_minutes) * 60
            if (now_utc - last) < timedelta(seconds=cooldown_seconds):
                return False, "blocked_cooldown"

        return True, "ok"

    def record_signal(self, timestamp_utc: datetime) -> None:
        day_key = timestamp_utc.strftime("%Y-%m-%d")
        counts = dict(self._state.get("daily_counts", {}))
        counts[day_key] = int(counts.get(day_key, 0)) + 1
        self._state["daily_counts"] = counts
        self._state["last_signal_utc"] = timestamp_utc.replace(tzinfo=timezone.utc).isoformat()
        self._save_state()

    def record_outcome(self, is_win: bool) -> None:
        streak = int(self._state.get("loss_streak", 0))
        self._state["loss_streak"] = 0 if is_win else streak + 1
        self._save_state()

    def reset_daily_if_needed(self, now_utc: datetime) -> None:
        counts = dict(self._state.get("daily_counts", {}))
        current_day = now_utc.strftime("%Y-%m-%d")
        keep = {current_day: int(counts.get(current_day, 0))}
        if counts != keep:
            self._state["daily_counts"] = keep
            self._save_state()

    def snapshot(self) -> dict[str, Any]:
        return {
            "max_signals_per_day": self.max_signals_per_day,
            "cooldown_candles": self.cooldown_candles,
            "max_loss_streak": self.max_loss_streak,
            "loss_streak": int(self._state.get("loss_streak", 0)),
            "last_signal_utc": self._state.get("last_signal_utc"),
            "daily_counts": dict(self._state.get("daily_counts", {})),
        }

    def _within_session(self, now_utc: datetime) -> bool:
        start = self._parse_hhmm(self.session_start_utc)
        end = self._parse_hhmm(self.session_end_utc)
        current_time = now_utc.time()
        if start <= end:
            return start <= current_time <= end
        return current_time >= start or current_time <= end

    @staticmethod
    def _parse_hhmm(raw: str):
        return datetime.strptime(raw, "%H:%M").time()

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"loss_streak": 0, "daily_counts": {}, "last_signal_utc": None}
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Invalid risk state file format")
            return payload
        except Exception:
            return {"loss_streak": 0, "daily_counts": {}, "last_signal_utc": None}

    def _save_state(self) -> None:
        self.state_path.write_text(json.dumps(self._state, indent=2), encoding="utf-8")
