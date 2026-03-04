from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd
import pytz

from signal_logic import TradeSignal


@dataclass
class RiskManager:
    """
    Applies hard risk filters, stores signal outcomes, and computes performance stats.
    """

    bdt_timezone: str = "Asia/Dhaka"
    max_consecutive_losses: int = 2
    max_signals_per_day: int = 2
    cooldown_candles: int = 5
    candle_minutes: int = 15
    session_start: str = "13:00"
    session_first_block_end: str = "13:15"
    session_last_block_start: str = "21:00"
    session_end: str = "21:30"
    loss_streak_path: str = "loss_streak.json"
    signals_path: str = "signals.csv"
    performance_path: str = "performance.csv"

    def __post_init__(self) -> None:
        self.tz = pytz.timezone(self.bdt_timezone)
        self._ensure_loss_file()
        self._ensure_signals_file()
        self.update_performance_csv()

    def can_send_signal(
        self,
        signal_time_utc: pd.Timestamp,
        atr_now: float,
        atr_previous: float,
    ) -> tuple[bool, str]:
        # Hard stop: if manual streak reaches threshold, no more signals are allowed.
        if self.get_consecutive_losses() >= self.max_consecutive_losses:
            return False, "Blocked: consecutive loss limit reached."

        signal_time_bdt = signal_time_utc.tz_convert(self.bdt_timezone)
        # Session filter:
        # - active window is 13:00-21:30 BDT
        # - first 15 minutes (13:00-13:15) are blocked
        # - last 30 minutes (21:00-21:30) are blocked
        if not self._is_allowed_session_time(signal_time_bdt.time()):
            return False, "Blocked: outside allowed BDT session window (13:15 to 21:00)."

        # Volatility contraction filter: skip when ATR weakens candle-to-candle.
        if atr_now < atr_previous:
            return False, "Blocked: ATR is contracting (ATR[-1] < ATR[-2])."

        # Daily exposure cap: 2 signals max per calendar day in BDT.
        day_count = self._daily_signal_count(signal_time_bdt.date())
        if day_count >= self.max_signals_per_day:
            return False, "Blocked: maximum 2 signals already sent today."

        # Cooldown filter: no signal inside last 5 candles (75 minutes).
        last_signal_time = self._last_signal_time_bdt()
        if last_signal_time is not None:
            cooldown_minutes = self.cooldown_candles * self.candle_minutes
            elapsed = signal_time_bdt - last_signal_time
            if elapsed < timedelta(minutes=cooldown_minutes):
                return False, f"Blocked: cooldown active ({elapsed} since previous signal)."

        return True, "All risk filters passed."

    def log_signal(self, signal: TradeSignal) -> None:
        signal_time_bdt = signal.timestamp_utc.tz_convert(self.bdt_timezone)
        row = pd.DataFrame(
            [
                {
                    "timestamp(BDT)": signal_time_bdt.strftime("%Y-%m-%d %H:%M:%S"),
                    "signal_type": signal.signal_type,
                    "entry_price": round(signal.entry_price, 4),
                    "1h_supertrend": signal.one_h_supertrend,
                    "15m_vwap": round(signal.fifteen_m_vwap, 4),
                    "reason_summary": signal.reason_summary,
                    "win_loss": "",
                }
            ]
        )

        path = Path(self.signals_path)
        existing = pd.read_csv(path)
        updated = pd.concat([existing, row], ignore_index=True)
        updated.to_csv(path, index=False)
        self.update_performance_csv()

    def set_consecutive_losses(self, value: int) -> None:
        safe_value = max(0, min(5, int(value)))
        path = Path(self.loss_streak_path)
        payload = {"consecutive_losses": safe_value, "updated_at_bdt": self._now_bdt_string()}
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self.update_performance_csv()

    def get_consecutive_losses(self) -> int:
        path = Path(self.loss_streak_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        return int(payload.get("consecutive_losses", 0))

    def mark_last_signal(self, outcome: str) -> tuple[bool, str]:
        outcome = outcome.strip().upper()
        if outcome not in {"WIN", "LOSS"}:
            return False, "Invalid outcome. Use WIN or LOSS."

        path = Path(self.signals_path)
        df = pd.read_csv(path)
        if df.empty:
            return False, "No signals found to update."

        pending_index = None
        for idx in range(len(df) - 1, -1, -1):
            value = str(df.at[idx, "win_loss"]).strip().upper()
            if value in {"", "NAN", "NONE", "NULL"}:
                pending_index = idx
                break
        if pending_index is None:
            return False, "No pending signal found (all are already marked)."

        df.at[pending_index, "win_loss"] = outcome
        df.to_csv(path, index=False)

        if outcome == "WIN":
            self.set_consecutive_losses(0)
        else:
            self.set_consecutive_losses(self.get_consecutive_losses() + 1)

        self.update_performance_csv()
        stamped = str(df.at[pending_index, "timestamp(BDT)"])
        return True, f"Updated signal {stamped} as {outcome}."

    def update_performance_csv(self) -> None:
        signals_df = pd.read_csv(self.signals_path)
        rows: list[dict[str, str | int | float]] = []
        now = self._now_bdt_string()
        streak = self.get_consecutive_losses()

        if not signals_df.empty:
            signals_df["timestamp(BDT)"] = pd.to_datetime(signals_df["timestamp(BDT)"], errors="coerce")
            signals_df = signals_df.dropna(subset=["timestamp(BDT)"]).copy()
            signals_df["date"] = signals_df["timestamp(BDT)"].dt.strftime("%Y-%m-%d")
            signals_df["win_loss"] = signals_df["win_loss"].astype(str).str.upper()

            for day, day_df in signals_df.groupby("date"):
                wins = int((day_df["win_loss"] == "WIN").sum())
                losses = int((day_df["win_loss"] == "LOSS").sum())
                decided = wins + losses
                win_rate = round((wins / decided) * 100.0, 2) if decided > 0 else 0.0
                rows.append(
                    {
                        "scope": "DAILY",
                        "date": day,
                        "total_signals": int(len(day_df)),
                        "wins": wins,
                        "losses": losses,
                        "win_rate_percent": win_rate,
                        "consecutive_losses": streak,
                        "updated_at_bdt": now,
                    }
                )

            wins_all = int((signals_df["win_loss"] == "WIN").sum())
            losses_all = int((signals_df["win_loss"] == "LOSS").sum())
            decided_all = wins_all + losses_all
            overall_rate = round((wins_all / decided_all) * 100.0, 2) if decided_all > 0 else 0.0
            rows.append(
                {
                    "scope": "OVERALL",
                    "date": "ALL",
                    "total_signals": int(len(signals_df)),
                    "wins": wins_all,
                    "losses": losses_all,
                    "win_rate_percent": overall_rate,
                    "consecutive_losses": streak,
                    "updated_at_bdt": now,
                }
            )
        else:
            rows.append(
                {
                    "scope": "OVERALL",
                    "date": "ALL",
                    "total_signals": 0,
                    "wins": 0,
                    "losses": 0,
                    "win_rate_percent": 0.0,
                    "consecutive_losses": streak,
                    "updated_at_bdt": now,
                }
            )

        pd.DataFrame(rows).to_csv(self.performance_path, index=False)

    def _ensure_loss_file(self) -> None:
        path = Path(self.loss_streak_path)
        if path.exists():
            return
        payload = {"consecutive_losses": 0, "updated_at_bdt": self._now_bdt_string()}
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _ensure_signals_file(self) -> None:
        path = Path(self.signals_path)
        if path.exists():
            return
        headers = [
            "timestamp(BDT)",
            "signal_type",
            "entry_price",
            "1h_supertrend",
            "15m_vwap",
            "reason_summary",
            "win_loss",
        ]
        pd.DataFrame(columns=headers).to_csv(path, index=False)

    def _daily_signal_count(self, day: date) -> int:
        df = pd.read_csv(self.signals_path)
        if df.empty:
            return 0
        times = pd.to_datetime(df["timestamp(BDT)"], errors="coerce")
        dates = times.dt.strftime("%Y-%m-%d")
        target = day.strftime("%Y-%m-%d")
        return int((dates == target).sum())

    def _last_signal_time_bdt(self) -> pd.Timestamp | None:
        df = pd.read_csv(self.signals_path)
        if df.empty:
            return None
        times = pd.to_datetime(df["timestamp(BDT)"], errors="coerce").dropna().sort_values()
        if times.empty:
            return None
        latest = times.iloc[-1]
        if latest.tzinfo is None:
            latest = self.tz.localize(latest.to_pydatetime())
            return pd.Timestamp(latest)
        return pd.Timestamp(latest).tz_convert(self.bdt_timezone)

    def _is_allowed_session_time(self, candle_time: time) -> bool:
        start = self._parse_time(self.session_start)
        first_block_end = self._parse_time(self.session_first_block_end)
        last_block_start = self._parse_time(self.session_last_block_start)
        end = self._parse_time(self.session_end)

        if candle_time < start or candle_time >= end:
            return False
        if start <= candle_time < first_block_end:
            return False
        if last_block_start <= candle_time < end:
            return False
        return True

    @staticmethod
    def _parse_time(raw: str) -> time:
        hour, minute = raw.split(":")
        return time(hour=int(hour), minute=int(minute))

    def _now_bdt_string(self) -> str:
        now = datetime.now(self.tz)
        return now.strftime("%Y-%m-%d %H:%M:%S")
