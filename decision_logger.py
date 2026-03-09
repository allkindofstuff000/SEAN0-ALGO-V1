from __future__ import annotations

import atexit
import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Any


LOGGER = logging.getLogger(__name__)
ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_TRACE_PATH = ROOT_DIR / "logs" / "decision_trace.log"


@dataclass
class DecisionLogger:
    path: Path = DEFAULT_TRACE_PATH
    max_queue_size: int = 2048
    batch_size: int = 100
    drain_timeout_seconds: float = 0.25
    _queue: Queue[dict[str, Any] | None] = field(init=False, repr=False)
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _start_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)
    _dropped: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._queue = Queue(maxsize=self.max_queue_size)
        atexit.register(self.close)

    def log_decision(self, payload: dict[str, Any]) -> None:
        if self._closed:
            return
        self._ensure_worker()
        record = dict(payload)
        record.setdefault("symbol", "UNKNOWN")
        record.setdefault("signal_modes", [])
        record.setdefault("decision", "accepted" if record.get("signal_generated") else "rejected")
        try:
            self._queue.put_nowait(self._normalize_value(record))
        except Full:
            self._dropped += 1
            if self._dropped == 1 or self._dropped % 100 == 0:
                LOGGER.warning("decision_logger_queue_full dropped=%s", self._dropped)

    def log_skip(self, symbol: str, reason: str, payload: dict[str, Any] | None = None) -> None:
        LOGGER.info("[SKIP] symbol=%s reason=%s", symbol, reason)
        record = dict(payload or {})
        record["symbol"] = symbol
        record["reason"] = reason
        record["signal_generated"] = False
        record["decision"] = "skipped"
        self.log_decision(record)

    def log_filter(self, symbol: str, name: str, payload: dict[str, Any] | None = None) -> None:
        details = dict(payload or {})
        if name == "Weak trend":
            LOGGER.info(
                "[FILTER] Weak trend detected symbol=%s ema_distance=%s atr=%s",
                symbol,
                details.get("ema_distance"),
                details.get("atr"),
            )
        else:
            LOGGER.info("[FILTER] %s symbol=%s", name, symbol)

    def log_trend(self, ema50: float, ema200: float) -> str:
        if ema50 > ema200:
            LOGGER.info("[TREND] EMA50 > EMA200 -> bullish bias (%.2f/%.2f)", ema50, ema200)
            return "bull"
        if ema50 < ema200:
            LOGGER.info("[TREND] EMA50 < EMA200 -> bearish bias (%.2f/%.2f)", ema50, ema200)
            return "bear"
        LOGGER.info("[TREND] EMA50 = EMA200 -> no clear bias (%.2f/%.2f)", ema50, ema200)
        return "flat"

    def log_rsi(
        self,
        rsi_value: float,
        trend: str,
        *,
        buy_threshold: float = 55.0,
        sell_threshold: float = 45.0,
    ) -> bool:
        normalized = trend.strip().lower()
        if normalized in {"bull", "buy"}:
            if rsi_value > buy_threshold:
                LOGGER.info("[MOMENTUM] RSI = %.2f -> buy momentum OK", rsi_value)
                return True
            LOGGER.info("[MOMENTUM] RSI = %.2f -> rejected (weak buy momentum)", rsi_value)
            return False
        if normalized in {"bear", "sell"}:
            if rsi_value < sell_threshold:
                LOGGER.info("[MOMENTUM] RSI = %.2f -> sell momentum OK", rsi_value)
                return True
            LOGGER.info("[MOMENTUM] RSI = %.2f -> rejected (weak sell momentum)", rsi_value)
            return False
        LOGGER.info("[MOMENTUM] RSI = %.2f -> skipped (no trend bias)", rsi_value)
        return False

    def log_volatility(self, atr: float, atr_avg: float) -> bool:
        if atr_avg > 0 and atr > atr_avg:
            LOGGER.info(
                "[VOLATILITY] ATR > ATR_AVG -> volatility expansion (%.2f/%.2f)",
                atr,
                atr_avg,
            )
            return True
        LOGGER.info("[VOLATILITY] ATR < ATR_AVG -> rejected (low volatility) (%.2f/%.2f)", atr, atr_avg)
        return False

    def log_session(self, session: str, allowed: bool) -> bool:
        label = session.replace("_", " ").title()
        if allowed:
            LOGGER.info("[SESSION] %s session -> allowed", label)
            return True
        LOGGER.info("[SESSION] %s session -> rejected", label)
        return False

    def log_breakout(self, close: float, prev_high: float, prev_low: float, trend: str) -> bool:
        normalized = trend.strip().lower()
        if normalized in {"bull", "buy"}:
            if close > prev_high:
                LOGGER.info("[BREAKOUT] Previous candle high broken (%.2f > %.2f)", close, prev_high)
                return True
            LOGGER.info("[BREAKOUT] No bullish breakout (%.2f <= %.2f)", close, prev_high)
            return False
        if normalized in {"bear", "sell"}:
            if close < prev_low:
                LOGGER.info("[BREAKOUT] Previous candle low broken (%.2f < %.2f)", close, prev_low)
                return True
            LOGGER.info("[BREAKOUT] No bearish breakout (%.2f >= %.2f)", close, prev_low)
            return False
        LOGGER.info("[BREAKOUT] No breakout -> rejected (no trend bias)")
        return False

    def log_result(self, signal: str | None, reason: str = "") -> None:
        if signal is not None and signal.strip().upper() in {"BUY", "SELL"}:
            LOGGER.info("[RESULT] %s SIGNAL", signal.strip().upper())
            return
        if reason:
            LOGGER.info("[RESULT] NO SIGNAL (%s)", reason)
            return
        LOGGER.info("[RESULT] NO SIGNAL")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._thread is None:
            return
        try:
            self._queue.put_nowait(None)
        except Full:
            pass
        self._thread.join(timeout=1.0)

    def _ensure_worker(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        with self._start_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._writer_loop,
                name="decision-trace-writer",
                daemon=True,
            )
            self._thread.start()

    def _writer_loop(self) -> None:
        stop_requested = False
        with self.path.open("a", encoding="utf-8") as handle:
            while True:
                if stop_requested and self._queue.empty():
                    break
                try:
                    item = self._queue.get(timeout=self.drain_timeout_seconds)
                except Empty:
                    continue
                if item is None:
                    stop_requested = True
                    continue

                batch = [item]
                while len(batch) < self.batch_size:
                    try:
                        next_item = self._queue.get_nowait()
                    except Empty:
                        break
                    if next_item is None:
                        stop_requested = True
                        break
                    batch.append(next_item)

                lines = [json.dumps(record, separators=(",", ":"), ensure_ascii=True) for record in batch]
                handle.write("\n".join(lines))
                handle.write("\n")
                handle.flush()

    @classmethod
    def _normalize_value(cls, value: Any) -> Any:
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {str(key): cls._normalize_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [cls._normalize_value(item) for item in value]
        return value


_default_logger: DecisionLogger | None = None
_default_logger_lock = threading.Lock()


def get_decision_logger() -> DecisionLogger:
    global _default_logger
    if _default_logger is not None:
        return _default_logger
    with _default_logger_lock:
        if _default_logger is None:
            _default_logger = DecisionLogger()
    return _default_logger
