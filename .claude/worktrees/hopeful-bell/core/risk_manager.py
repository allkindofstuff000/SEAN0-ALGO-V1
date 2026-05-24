from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .signal_logic import TradeSignal


PROJECT_ROOT = Path(__file__).resolve().parent.parent


SIGNAL_FIELDS = [
    "timestamp_utc",
    "symbol",
    "signal_type",
    "entry_price",
    "score",
    "reason_summary",
    "outcome",
]


@dataclass
class RiskManager:
    """
    Minimal operational risk layer for the XAU-only MVP.

    Controls:
    - max setups per day
    - cooldown in entry candles
    - max manual loss streak
    """

    max_signals_per_day: int = 3
    cooldown_candles: int = 1
    max_loss_streak: int = 2
    state_path: Path = PROJECT_ROOT / "risk_state.json"
    signals_path: Path = PROJECT_ROOT / "signals.csv"
    performance_path: Path = PROJECT_ROOT / "performance.csv"
    _state: dict[str, Any] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state = self._load_state()
        self._ensure_signal_file()
        self.update_performance_csv()

    def can_emit_signal(self, signal: TradeSignal) -> tuple[bool, str]:
        timestamp_utc = self._coerce_datetime(signal.timestamp_utc)
        if self.get_consecutive_losses() >= self.max_loss_streak:
            return False, "blocked_max_loss_streak"

        current_day = timestamp_utc.astimezone(timezone.utc).strftime("%Y-%m-%d")
        daily_counts = self._state.get("daily_counts", {})
        sent_today = int(daily_counts.get(current_day, 0))
        if sent_today >= self.max_signals_per_day:
            return False, "blocked_max_signals_per_day"

        last_signal_iso = str(self._state.get("last_signal_utc", "") or "")
        if last_signal_iso:
            last_signal = datetime.fromisoformat(last_signal_iso)
            cooldown_minutes = self.cooldown_candles * signal.entry_timeframe_minutes
            if timestamp_utc - last_signal < timedelta(minutes=cooldown_minutes):
                return False, "blocked_cooldown_candles"

        return True, "ok"

    def record_signal(self, signal: TradeSignal) -> None:
        timestamp_utc = self._coerce_datetime(signal.timestamp_utc)
        day_key = timestamp_utc.astimezone(timezone.utc).strftime("%Y-%m-%d")
        daily_counts = {str(key): int(value) for key, value in self._state.get("daily_counts", {}).items()}
        daily_counts[day_key] = int(daily_counts.get(day_key, 0)) + 1
        self._state["daily_counts"] = daily_counts
        self._state["last_signal_utc"] = timestamp_utc.astimezone(timezone.utc).isoformat()
        self._save_state()
        self._append_signal(signal)
        self.update_performance_csv()

    def record_outcome(self, is_win: bool) -> None:
        current_streak = self.get_consecutive_losses()
        self._state["loss_streak"] = 0 if is_win else current_streak + 1
        self._save_state()
        self.update_performance_csv()

    def set_consecutive_losses(self, value: int) -> None:
        self._state["loss_streak"] = max(0, int(value))
        self._save_state()
        self.update_performance_csv()

    def get_consecutive_losses(self) -> int:
        return int(self._state.get("loss_streak", 0))

    def mark_last_signal(self, outcome: str) -> tuple[bool, str]:
        normalized = outcome.strip().upper()
        if normalized not in {"WIN", "LOSS"}:
            return False, "Invalid outcome. Use WIN or LOSS."

        rows = self._read_signal_rows()
        pending_index = None
        for index in range(len(rows) - 1, -1, -1):
            if not str(rows[index].get("outcome", "")).strip():
                pending_index = index
                break

        if pending_index is None:
            return False, "No pending signal found."

        rows[pending_index]["outcome"] = normalized
        self._write_signal_rows(rows)
        self.record_outcome(is_win=normalized == "WIN")
        return True, f"Marked signal {rows[pending_index]['timestamp_utc']} as {normalized}."

    def update_performance_csv(self) -> None:
        rows = self._read_signal_rows()
        wins = sum(1 for row in rows if str(row.get("outcome", "")).upper() == "WIN")
        losses = sum(1 for row in rows if str(row.get("outcome", "")).upper() == "LOSS")
        decided = wins + losses
        total = len(rows)
        average_score = round(
            sum(float(row.get("score", 0.0) or 0.0) for row in rows) / total,
            2,
        ) if total else 0.0
        payload = [
            {
                "scope": "OVERALL",
                "date": "ALL",
                "total_signals": total,
                "wins": wins,
                "losses": losses,
                "win_rate_percent": round((wins / decided) * 100.0, 2) if decided else 0.0,
                "average_score": average_score,
                "consecutive_losses": self.get_consecutive_losses(),
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            }
        ]
        with self.performance_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "scope",
                    "date",
                    "total_signals",
                    "wins",
                    "losses",
                    "win_rate_percent",
                    "average_score",
                    "consecutive_losses",
                    "updated_at_utc",
                ],
            )
            writer.writeheader()
            writer.writerows(payload)

    def snapshot(self) -> dict[str, Any]:
        return {
            "max_signals_per_day": self.max_signals_per_day,
            "cooldown_candles": self.cooldown_candles,
            "max_loss_streak": self.max_loss_streak,
            "loss_streak": self.get_consecutive_losses(),
            "last_signal_utc": self._state.get("last_signal_utc"),
            "daily_counts": dict(self._state.get("daily_counts", {})),
        }

    def _load_state(self) -> dict[str, Any]:
        default_state = {"loss_streak": 0, "daily_counts": {}, "last_signal_utc": None}
        if not self.state_path.exists():
            return default_state
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("invalid_risk_state")

            payload.setdefault("loss_streak", 0)
            payload.setdefault("daily_counts", {})
            payload.setdefault("last_signal_utc", None)

            legacy_last_signal_by_symbol = payload.pop("last_signal_utc_by_symbol", None)
            if not payload.get("last_signal_utc") and isinstance(legacy_last_signal_by_symbol, dict):
                payload["last_signal_utc"] = (
                    legacy_last_signal_by_symbol.get("XAUUSDT")
                    or legacy_last_signal_by_symbol.get("XAUUSD")
                )

            legacy_daily_counts = payload.get("daily_counts", {})
            normalized_daily_counts: dict[str, int] = {}
            if isinstance(legacy_daily_counts, dict):
                for day_key, day_value in legacy_daily_counts.items():
                    if isinstance(day_value, dict):
                        normalized_daily_counts[str(day_key)] = int(
                            day_value.get("XAUUSDT", day_value.get("XAUUSD", 0))
                        )
                    else:
                        normalized_daily_counts[str(day_key)] = int(day_value)
            payload["daily_counts"] = normalized_daily_counts
            return payload
        except Exception:
            return default_state

    def _save_state(self) -> None:
        self.state_path.write_text(json.dumps(self._state, indent=2), encoding="utf-8")

    @staticmethod
    def _coerce_datetime(value: datetime | pd.Timestamp) -> datetime:
        if isinstance(value, pd.Timestamp):
            dt = value.to_pydatetime()
        else:
            dt = value
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def _ensure_signal_file(self) -> None:
        if self.signals_path.exists():
            rows = self._read_signal_rows()
            self._write_signal_rows(rows)
            return
        with self.signals_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=SIGNAL_FIELDS)
            writer.writeheader()

    def _append_signal(self, signal: TradeSignal) -> None:
        rows = self._read_signal_rows()
        rows.append(
            {
                "timestamp_utc": signal.timestamp_utc.isoformat(),
                "symbol": signal.symbol,
                "signal_type": signal.signal_type,
                "entry_price": f"{signal.entry_price:.4f}",
                "score": signal.score,
                "reason_summary": signal.reason_summary,
                "outcome": "",
            }
        )
        self._write_signal_rows(rows)

    def _read_signal_rows(self) -> list[dict[str, str]]:
        if not self.signals_path.exists():
            return []
        with self.signals_path.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        return [{field: str(row.get(field, "") or "") for field in SIGNAL_FIELDS} for row in rows]

    def _write_signal_rows(self, rows: list[dict[str, str]]) -> None:
        with self.signals_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=SIGNAL_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
