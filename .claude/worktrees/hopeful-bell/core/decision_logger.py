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
ROOT_DIR = Path(__file__).resolve().parent.parent
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

    def log_market_snapshot(self, payload: dict[str, Any]) -> None:
        record = dict(payload)
        record.setdefault("event_type", "market_snapshot")
        record.setdefault("timestamp", record.get("fetched_at_utc"))
        record.setdefault("signal_generated", False)
        record.setdefault("decision", "market_snapshot")
        record.setdefault("signal_modes", [])

        LOGGER.info(
            "[MARKET] fetched_at=%s live_price=%s live_candle_open=%s complete=%s status=%s entry_closed_until=%s trend_closed_until=%s",
            record.get("fetched_at_utc"),
            record.get("live_price"),
            record.get("live_candle_open_utc"),
            record.get("live_candle_complete"),
            record.get("feed_status"),
            record.get("latest_closed_entry_close_utc"),
            record.get("latest_closed_trend_close_utc"),
        )
        self.log_decision(record)

    def log_trend(self, ema50: float, ema200: float) -> str:
        if ema50 > ema200:
            LOGGER.info("[TREND] EMA50 > EMA200 -> bullish bias (%.2f/%.2f)", ema50, ema200)
            return "bull"
        if ema50 < ema200:
            LOGGER.info("[TREND] EMA50 < EMA200 -> bearish bias (%.2f/%.2f)", ema50, ema200)
            return "bear"
        LOGGER.info("[TREND] EMA50 = EMA200 -> no clear bias (%.2f/%.2f)", ema50, ema200)
        return "flat"

    def log_regime(self, regime: str, confidence: float) -> str:
        normalized = str(regime).strip().lower()
        if normalized == "trend":
            LOGGER.info("[REGIME] trend detected confidence=%.2f", confidence)
        elif normalized == "range":
            LOGGER.info("[REGIME] ranging market detected confidence=%.2f", confidence)
        elif normalized == "high_volatility":
            LOGGER.info("[REGIME] high volatility detected confidence=%.2f", confidence)
        elif normalized == "low_volatility":
            LOGGER.info("[REGIME] low volatility detected confidence=%.2f", confidence)
        else:
            LOGGER.info("[REGIME] %s detected confidence=%.2f", regime, confidence)
        return normalized

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

    def log_volatility(self, atr: float, atr_avg: float, *, threshold_multiplier: float = 1.0) -> bool:
        threshold = atr_avg * threshold_multiplier
        if threshold > 0 and atr > threshold:
            LOGGER.info(
                "[VOLATILITY] ATR > ATR_AVG*%.2f -> volatility expansion (%.2f/%.2f)",
                threshold_multiplier,
                atr,
                threshold,
            )
            return True
        LOGGER.info(
            "[VOLATILITY] ATR < ATR_AVG*%.2f -> rejected (low volatility) (%.2f/%.2f)",
            threshold_multiplier,
            atr,
            threshold,
        )
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

    def log_htf(self, ema50: float, ema200: float, direction: str) -> str:
        """Log the HTF (1H) structure filter evaluation. Returns bias string."""
        if ema50 > ema200:
            bias = "bullish"
            LOGGER.info("[HTF] H1 EMA50=%.2f EMA200=%.2f -> bullish bias -> BUY allowed", ema50, ema200)
        elif ema50 < ema200:
            bias = "bearish"
            LOGGER.info("[HTF] H1 EMA50=%.2f EMA200=%.2f -> bearish bias -> SELL allowed", ema50, ema200)
        else:
            bias = "neutral"
            LOGGER.info("[HTF] H1 EMA50=%.2f EMA200=%.2f -> neutral -> no clear direction", ema50, ema200)
        normalized_dir = (direction or "").strip().upper()
        if normalized_dir in {"BUY", "SELL"}:
            htf_aligned = (normalized_dir == "BUY" and bias == "bullish") or (
                normalized_dir == "SELL" and bias == "bearish"
            )
            if htf_aligned:
                LOGGER.info("[HTF] aligned with %s signal -> trade allowed", normalized_dir)
            else:
                LOGGER.info("[HTF] Conflict with HTF trend -> trade rejected")
        return bias

    def log_range_setup(self, direction: str | None, close: float, prev_high: float, prev_low: float, rsi_value: float) -> bool:
        normalized = (direction or "").strip().upper()
        if normalized == "BUY":
            LOGGER.info(
                "[RANGE] mean reversion BUY setup (close=%.2f prev_low=%.2f rsi=%.2f)",
                close,
                prev_low,
                rsi_value,
            )
            return True
        if normalized == "SELL":
            LOGGER.info(
                "[RANGE] mean reversion SELL setup (close=%.2f prev_high=%.2f rsi=%.2f)",
                close,
                prev_high,
                rsi_value,
            )
            return True
        LOGGER.info(
            "[RANGE] no mean reversion setup (close=%.2f prev_high=%.2f prev_low=%.2f rsi=%.2f)",
            close,
            prev_high,
            prev_low,
            rsi_value,
        )
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
