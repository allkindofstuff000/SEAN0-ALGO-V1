from __future__ import annotations

import csv
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

import pandas as pd

from storage import read_json_with_fallback, write_json_locked

if TYPE_CHECKING:
    from bot.signals.signal_logic import TradingSignal


LOGGER = logging.getLogger(__name__)


@dataclass
class PerformanceTracker:
    """
    Track pending and completed trades for the adaptive learning system.

    Completed trades are appended to `data/trade_log.csv`. Pending signals are
    kept in a lightweight JSON state file so the main loop can settle them on
    subsequent cycles without blocking the signal path.
    """

    trade_log_path: Path
    pending_trades_path: Path
    forex_max_holding_minutes: int = 240
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        self.trade_log_path.parent.mkdir(parents=True, exist_ok=True)
        self.pending_trades_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_trade_log_file()
        self._ensure_pending_state()

    def register_signal(self, signal: TradingSignal, routed_signal: dict[str, Any]) -> dict[str, Any]:
        record = self._build_pending_record(signal=signal, routed_signal=routed_signal)
        with self._lock:
            state = self._load_pending_state()
            trades = list(state.get("trades", []))
            trades.append(record)
            self._save_pending_state({"trades": trades, "updated_at_utc": datetime.now(timezone.utc).isoformat()})
        LOGGER.info(
            "learning_signal_registered trade_id=%s mode=%s score=%s",
            record["trade_id"],
            record["mode"],
            record["score"],
        )
        return record

    def settle_due_trades(self, *, current_time_utc: datetime, latest_candle: dict[str, Any]) -> list[dict[str, Any]]:
        current_time = self._coerce_datetime(current_time_utc)
        completed: list[dict[str, Any]] = []
        remaining: list[dict[str, Any]] = []
        latest_close = float(latest_candle["close"])
        latest_high = float(latest_candle.get("high", latest_close))
        latest_low = float(latest_candle.get("low", latest_close))

        with self._lock:
            state = self._load_pending_state()
            for trade in state.get("trades", []):
                settlement = self._resolve_trade_result(
                    trade=trade,
                    current_time_utc=current_time,
                    latest_close=latest_close,
                    latest_high=latest_high,
                    latest_low=latest_low,
                )
                if settlement is None:
                    remaining.append(trade)
                    continue
                completed.append(settlement)

            if completed:
                self._append_completed_rows(completed)
            self._save_pending_state({"trades": remaining, "updated_at_utc": current_time.isoformat()})

        if completed:
            LOGGER.info("learning_trades_settled count=%s", len(completed))
        return completed

    def completed_trade_count(self) -> int:
        with self._lock:
            if not self.trade_log_path.exists():
                return 0
            with self.trade_log_path.open("r", newline="", encoding="utf-8") as handle:
                return max(0, sum(1 for _ in handle) - 1)

    def load_recent_trades(self, limit: int = 100) -> pd.DataFrame:
        with self._lock:
            if not self.trade_log_path.exists():
                return pd.DataFrame(columns=self._trade_log_headers())
            frame = pd.read_csv(self.trade_log_path)
        if frame.empty:
            return frame
        frame["score"] = pd.to_numeric(frame["score"], errors="coerce").fillna(0)
        frame["result"] = frame["result"].astype(str).str.upper()
        return frame.tail(limit).reset_index(drop=True)

    def _build_pending_record(self, *, signal: TradingSignal, routed_signal: dict[str, Any]) -> dict[str, Any]:
        mode = str(routed_signal.get("mode", "binary")).lower()
        opened_at = signal.timestamp_utc.astimezone(timezone.utc)
        base = {
            "trade_id": f"{signal.pair}-{int(opened_at.timestamp())}-{signal.score}",
            "timestamp": opened_at.isoformat(),
            "pair": signal.pair,
            "mode": mode,
            "direction": signal.direction,
            "score": int(signal.score),
            "session": signal.session,
            "regime": signal.regime,
            "timeframe": signal.timeframe,
            "entry_price": float(signal.price),
            "threshold": int(getattr(signal, "score_threshold", 0)),
        }
        if mode == "binary":
            expiry_minutes = self._parse_expiry_minutes(str(routed_signal.get("expiry", "30m")))
            base.update(
                {
                    "expiry": str(routed_signal.get("expiry", "30m")),
                    "expiry_minutes": expiry_minutes,
                    "expires_at_utc": (opened_at + timedelta(minutes=expiry_minutes)).isoformat(),
                }
            )
            return base

        base.update(
            {
                "entry": float(routed_signal.get("entry", signal.price)),
                "stop_loss": float(routed_signal.get("stop_loss", signal.price)),
                "take_profit": float(routed_signal.get("take_profit", signal.price)),
                "max_holding_minutes": int(self.forex_max_holding_minutes),
                "expires_at_utc": (opened_at + timedelta(minutes=int(self.forex_max_holding_minutes))).isoformat(),
            }
        )
        return base

    def _resolve_trade_result(
        self,
        *,
        trade: dict[str, Any],
        current_time_utc: datetime,
        latest_close: float,
        latest_high: float,
        latest_low: float,
    ) -> dict[str, Any] | None:
        expires_at = self._coerce_datetime(trade.get("expires_at_utc"))
        mode = str(trade.get("mode", "binary")).lower()
        result: str | None = None

        if mode == "binary":
            if current_time_utc < expires_at:
                return None
            result = self._settle_binary(trade=trade, settlement_price=latest_close)
        else:
            result = self._settle_forex(
                trade=trade,
                current_time_utc=current_time_utc,
                latest_close=latest_close,
                latest_high=latest_high,
                latest_low=latest_low,
                expires_at=expires_at,
            )
            if result is None:
                return None

        return {
            "timestamp": trade.get("timestamp"),
            "closed_at_utc": current_time_utc.isoformat(),
            "pair": trade.get("pair"),
            "mode": mode,
            "direction": trade.get("direction"),
            "score": int(trade.get("score", 0)),
            "result": result,
            "session": trade.get("session"),
            "regime": trade.get("regime"),
            "timeframe": trade.get("timeframe"),
            "entry_price": float(trade.get("entry_price", trade.get("entry", 0.0))),
            "threshold": trade.get("threshold"),
        }

    def _settle_binary(self, *, trade: dict[str, Any], settlement_price: float) -> str:
        entry_price = float(trade.get("entry_price", 0.0))
        direction = str(trade.get("direction", "CALL")).upper()
        if direction == "CALL":
            return "WIN" if settlement_price > entry_price else "LOSS"
        return "WIN" if settlement_price < entry_price else "LOSS"

    def _settle_forex(
        self,
        *,
        trade: dict[str, Any],
        current_time_utc: datetime,
        latest_close: float,
        latest_high: float,
        latest_low: float,
        expires_at: datetime,
    ) -> str | None:
        entry = float(trade.get("entry", trade.get("entry_price", 0.0)))
        stop_loss = float(trade.get("stop_loss", entry))
        take_profit = float(trade.get("take_profit", entry))
        direction = str(trade.get("direction", "CALL")).upper()
        buy_side = direction in {"CALL", "BUY"}

        stop_hit = latest_low <= stop_loss if buy_side else latest_high >= stop_loss
        target_hit = latest_high >= take_profit if buy_side else latest_low <= take_profit

        if stop_hit and target_hit:
            return "LOSS"
        if target_hit:
            return "WIN"
        if stop_hit:
            return "LOSS"
        if current_time_utc < expires_at:
            return None

        if buy_side:
            return "WIN" if latest_close > entry else "LOSS"
        return "WIN" if latest_close < entry else "LOSS"

    def _ensure_trade_log_file(self) -> None:
        if self.trade_log_path.exists():
            return
        with self.trade_log_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=self._trade_log_headers())
            writer.writeheader()

    def _ensure_pending_state(self) -> None:
        if self.pending_trades_path.exists():
            return
        self._save_pending_state({"trades": [], "updated_at_utc": datetime.now(timezone.utc).isoformat()})

    def _append_completed_rows(self, rows: list[dict[str, Any]]) -> None:
        with self.trade_log_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=self._trade_log_headers())
            for row in rows:
                writer.writerow(row)

    def _load_pending_state(self) -> dict[str, Any]:
        return read_json_with_fallback(
            self.pending_trades_path,
            {"trades": [], "updated_at_utc": None},
        )

    def _save_pending_state(self, payload: dict[str, Any]) -> None:
        write_json_locked(self.pending_trades_path, payload)

    @staticmethod
    def _trade_log_headers() -> list[str]:
        return [
            "timestamp",
            "closed_at_utc",
            "pair",
            "mode",
            "direction",
            "score",
            "result",
            "session",
            "regime",
            "timeframe",
            "entry_price",
            "threshold",
        ]

    @staticmethod
    def _parse_expiry_minutes(raw_expiry: str) -> int:
        raw = raw_expiry.strip().lower()
        if raw.endswith("m"):
            return max(1, int(raw[:-1]))
        if raw.endswith("h"):
            return max(1, int(raw[:-1]) * 60)
        return max(1, int(raw))

    @staticmethod
    def _coerce_datetime(raw_value: Any) -> datetime:
        if isinstance(raw_value, datetime):
            return raw_value.astimezone(timezone.utc) if raw_value.tzinfo else raw_value.replace(tzinfo=timezone.utc)
        if raw_value is None:
            return datetime.now(timezone.utc)
        return datetime.fromisoformat(str(raw_value)).astimezone(timezone.utc)

