from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot.output.telegram_bot import TelegramNotifier
from bot.signals.signal_logic import TradingSignal


LOGGER = logging.getLogger(__name__)


@dataclass
class SignalDispatcher:
    """
    Dispatch signals to Telegram and persist signal/performance history.
    """

    notifier: TelegramNotifier
    signal_history_path: Path
    performance_path: Path

    def __post_init__(self) -> None:
        self.signal_history_path.parent.mkdir(parents=True, exist_ok=True)
        self.performance_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_signal_history_file()
        self._ensure_performance_file()

    async def dispatch(self, signal: TradingSignal, routed_signal: dict[str, Any]) -> dict[str, Any]:
        message = self.format_message(signal=signal, routed_signal=routed_signal)
        sent = await self.notifier.send_message(message)
        self._append_signal_row(signal)
        perf = self._refresh_performance()
        LOGGER.info(
            "signal_dispatched mode=%s direction=%s score=%s sent=%s",
            routed_signal.get("mode"),
            signal.direction,
            signal.score,
            sent,
        )
        return {"sent": sent, "performance": perf, "payload": routed_signal}

    @staticmethod
    def format_message(signal: TradingSignal, routed_signal: dict[str, Any]) -> str:
        mode = str(routed_signal.get("mode", "binary")).lower()
        if mode == "forex":
            return (
                "SIGNAL\n"
                f"Mode: FOREX\n"
                f"Pair: {routed_signal['pair']}\n"
                f"Direction: {routed_signal['direction']}\n"
                f"Entry: {routed_signal['entry']:.4f}\n"
                f"Stop Loss: {routed_signal['stop_loss']:.4f}\n"
                f"Take Profit: {routed_signal['take_profit']:.4f}\n"
                f"Score: {signal.score}\n"
                f"Regime: {signal.regime}\n"
                f"Session: {signal.session}\n"
                f"Timeframe: {signal.timeframe}"
            )

        return (
            "SIGNAL\n"
            f"Mode: BINARY\n"
            f"Pair: {routed_signal['pair']}\n"
            f"Direction: {routed_signal['direction']}\n"
            f"Expiry: {routed_signal['expiry']}\n"
            f"Score: {signal.score}\n"
            f"Regime: {signal.regime}\n"
            f"Session: {signal.session}\n"
            f"Timeframe: {signal.timeframe}\n"
            f"Price: {signal.price:.2f}"
        )

    def read_signals(self, limit: int = 100) -> list[dict[str, str]]:
        if not self.signal_history_path.exists():
            return []
        with self.signal_history_path.open("r", newline="", encoding="utf-8") as file_handle:
            rows = list(csv.DictReader(file_handle))
        return rows[-limit:]

    def read_performance(self) -> dict[str, Any]:
        if not self.performance_path.exists():
            return {}
        try:
            return json.loads(self.performance_path.read_text(encoding="utf-8"))
        except Exception:
            LOGGER.exception("read_performance_failed")
            return {}

    def _ensure_signal_history_file(self) -> None:
        if self.signal_history_path.exists():
            return
        with self.signal_history_path.open("w", newline="", encoding="utf-8") as file_handle:
            writer = csv.DictWriter(
                file_handle,
                fieldnames=[
                    "timestamp_utc",
                    "pair",
                    "direction",
                    "score",
                    "regime",
                    "timeframe",
                    "price",
                    "reason",
                ],
            )
            writer.writeheader()

    def _ensure_performance_file(self) -> None:
        if self.performance_path.exists():
            return
        payload = {
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "total_signals": 0,
            "call_signals": 0,
            "put_signals": 0,
            "average_score": 0.0,
            "last_signal_utc": None,
        }
        self.performance_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _append_signal_row(self, signal: TradingSignal) -> None:
        with self.signal_history_path.open("a", newline="", encoding="utf-8") as file_handle:
            writer = csv.DictWriter(
                file_handle,
                fieldnames=[
                    "timestamp_utc",
                    "pair",
                    "direction",
                    "score",
                    "regime",
                    "timeframe",
                    "price",
                    "reason",
                ],
            )
            writer.writerow(
                {
                    "timestamp_utc": signal.timestamp_utc.isoformat(),
                    "pair": signal.pair,
                    "direction": signal.direction,
                    "score": signal.score,
                    "regime": signal.regime,
                    "timeframe": signal.timeframe,
                    "price": f"{signal.price:.6f}",
                    "reason": signal.reason,
                }
            )

    def _refresh_performance(self) -> dict[str, Any]:
        rows = self.read_signals(limit=100000)
        total = len(rows)
        call_count = sum(1 for row in rows if str(row.get("direction", "")).upper() == "CALL")
        put_count = sum(1 for row in rows if str(row.get("direction", "")).upper() == "PUT")
        average_score = 0.0
        if total:
            scores = [float(row.get("score", 0.0) or 0.0) for row in rows]
            average_score = round(sum(scores) / len(scores), 2)
        payload = {
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "total_signals": total,
            "call_signals": call_count,
            "put_signals": put_count,
            "average_score": average_score,
            "last_signal_utc": rows[-1]["timestamp_utc"] if rows else None,
        }
        self.performance_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload
