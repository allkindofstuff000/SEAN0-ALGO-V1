"""
XAU SCALP STRATEGY — ICT orchestrator.
Reuses the shared OANDA candle engine and existing dashboard/API routes.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time as _time
from collections import deque
from typing import Any

from . import config as cfg
from .ict_entry_engine import ICTEntryEngine
from .utils import normalize_candle, normalize_candles

# MongoDB persistence (non-fatal)
try:
    from core.mongo_store import save_strategy_signal as _save_signal
except Exception:
    def _save_signal(_): return None

log = logging.getLogger(__name__)


class XauScalpSignal:
    def __init__(self, stream_engine=None):
        self._engine = stream_engine
        self._entry_engine = ICTEntryEngine(cfg)
        self._candles_m1: list[dict[str, Any]] = []
        self._candles_m5: list[dict[str, Any]] = []
        self._candles_m15: list[dict[str, Any]] = []
        self._latest_signal: dict[str, Any] | None = None
        self._signal_history: deque = deque(maxlen=200)
        self._sub_id: int | None = None
        self._queue = None
        self._live_price = 0.0
        self._live_time = 0
        self._initialized = False
        self._paused = False
        self._current_conditions: dict[str, Any] = {}
        self._last_reason = "Waiting for first evaluation"
        self._pending_eval_time: int | None = None
        self._stats = {
            "totalSignals": 0,
            "strongSignals": 0,
            "mediumSignals": 0,
            "lastSignalTime": None,
        }
        self._decision_log: deque = deque(maxlen=500)

    async def initialize(self):
        if not self._engine:
            log.warning("XauScalpSignal: no stream engine — running in offline mode")
            return

        try:
            self._candles_m1 = normalize_candles(self._engine.store.get_candles("M1"))[-cfg.CANDLE_LOOKBACK_M1:]
            self._candles_m5 = normalize_candles(self._engine.store.get_candles("M5"))[-cfg.CANDLE_LOOKBACK_M5:]
            self._candles_m15 = normalize_candles(self._engine.store.get_candles("M15"))[-cfg.CANDLE_LOOKBACK_M15:]
            self._refresh_conditions()

            self._sub_id, self._queue = self._engine.subscribe()
            asyncio.create_task(self._event_loop(), name="xau_scalp_events")
            self._initialized = True
            log.info(
                "XauScalpSignal initialised ✓ M1=%s M5=%s M15=%s",
                len(self._candles_m1),
                len(self._candles_m5),
                len(self._candles_m15),
            )
        except Exception as exc:
            log.error("XauScalpSignal.initialize error: %s", exc)

    def stop(self):
        """Pause the strategy — stops processing candles and evaluating signals."""
        self._paused = True
        self._initialized = False
        log.info("XauScalpSignal stopped (paused)")

    async def resume(self):
        """Resume the strategy after a stop/pause."""
        if not self._engine:
            log.warning("XauScalpSignal: no stream engine — cannot resume")
            return
        self._paused = False
        self._initialized = True
        log.info("XauScalpSignal resumed")

    async def _event_loop(self):
        while True:
            try:
                raw = await asyncio.wait_for(self._queue.get(), timeout=60.0)
                # Always consume queue items to prevent backlog, but skip processing when paused
                if self._paused:
                    continue
                event = json.loads(raw)
                etype = event.get("type")

                if etype == "candle":
                    timeframe = event.get("timeframe")
                    candle = event.get("candle")
                    if candle:
                        self._on_closed_candle(timeframe, candle)

                elif etype == "tick":
                    self._live_price = float(event.get("price", self._live_price))
                    self._live_time = int(event.get("time", self._live_time))

            except asyncio.TimeoutError:
                pass
            except Exception as exc:
                log.error("XauScalpSignal event loop error: %s", exc)
                await asyncio.sleep(1)

    def _on_closed_candle(self, timeframe: str, candle: dict[str, Any]):
        normalized = normalize_candle(candle)
        normalized["timeframe"] = timeframe
        if timeframe == "M1":
            self._candles_m1 = self._append_candle(self._candles_m1, normalized, cfg.CANDLE_LOOKBACK_M1)
            if normalized["time"] % 300 == 0:
                self._pending_eval_time = normalized["time"]
            else:
                self._evaluate_latest()
        elif timeframe == "M5":
            self._candles_m5 = self._append_candle(self._candles_m5, normalized, cfg.CANDLE_LOOKBACK_M5)
            if self._pending_eval_time == normalized["time"] and normalized["time"] % 900 != 0:
                self._evaluate_latest()
                self._pending_eval_time = None
        elif timeframe == "M15":
            self._candles_m15 = self._append_candle(self._candles_m15, normalized, cfg.CANDLE_LOOKBACK_M15)
            if self._pending_eval_time == normalized["time"]:
                self._evaluate_latest()
                self._pending_eval_time = None

    def _append_candle(self, candles: list[dict[str, Any]], candle: dict[str, Any], limit: int) -> list[dict[str, Any]]:
        updated = [c for c in candles if c["time"] != candle["time"]]
        updated.append(candle)
        updated.sort(key=lambda item: item["time"])
        return updated[-limit:]

    def _evaluate_latest(self):
        result = self._refresh_conditions()
        signal = result.get("signal")
        candle = self._candles_m1[-1] if self._candles_m1 else {"time": 0, "close": self._live_price}

        self._decision_log.appendleft(
            {
                "ts": int(_time.time() * 1000),
                "candleTime": candle.get("time"),
                "price": round(float(candle.get("close", self._live_price or 0.0)), 2),
                "reason": result.get("reason"),
                "decision": "SIGNAL" if signal else "NO_SIGNAL",
                "score": result.get("score", 0),
                "strength": signal.get("strength") if signal else None,
                "direction": signal.get("type") if signal else None,
                "conditions": {key: bool(value.get("active")) for key, value in self._current_conditions.items()},
                "session": self._current_conditions.get("session", {}).get("zoneName"),
                "bias": self._current_conditions.get("bias15m", {}).get("headline"),
            }
        )

        if signal:
            self._latest_signal = signal
            self._signal_history.appendleft(signal)
            self._stats["totalSignals"] += 1
            if signal["strength"] == "STRONG":
                self._stats["strongSignals"] += 1
            else:
                self._stats["mediumSignals"] += 1
            self._stats["lastSignalTime"] = signal["timestamp"]
            log.info(
                "XauScalpSignal [%s] %s @ %.2f SL %.2f TP1 %.2f TP2 %.2f",
                signal["strength"],
                signal["type"],
                signal["entryPrice"],
                signal["stopLoss"],
                signal["takeProfit1"],
                signal["takeProfit2"],
            )
            # Persist to MongoDB
            try:
                _save_signal({
                    "strategy":      "xau-scalp",
                    "symbol":        "XAUUSD",
                    "direction":     signal["type"],
                    "entry_price":   signal["entryPrice"],
                    "stop_loss":     signal["stopLoss"],
                    "take_profit":   signal["takeProfit1"],
                    "take_profit_2": signal["takeProfit2"],
                    "break_even":    signal.get("breakEven"),
                    "score":         signal.get("score", 0),
                    "strength":      signal["strength"],
                    "session":       self._current_conditions.get("session", {}).get("zoneName", ""),
                    "timestamp":     signal.get("timestamp"),
                })
            except Exception as exc:
                log.warning("[XAU-SCALP] MongoDB signal save failed: %s", exc)

    def _refresh_conditions(self) -> dict[str, Any]:
        current_tick = {
            "price": self._live_price or (self._candles_m1[-1]["close"] if self._candles_m1 else 0.0),
            "time": self._live_time or (self._candles_m1[-1]["time"] if self._candles_m1 else 0),
        }
        result = self._entry_engine.evaluate(self._candles_m15, self._candles_m5, self._candles_m1, current_tick)
        self._current_conditions = result.get("conditions", {})
        self._last_reason = result.get("reason", self._last_reason)
        return result

    def get_latest_signal(self) -> dict[str, Any] | None:
        return self._latest_signal

    def get_signal_history(self, limit: int = 20) -> list[dict[str, Any]]:
        return list(self._signal_history)[:limit]

    def get_strategy_stats(self) -> dict[str, Any]:
        return {
            **self._stats,
            "candleCount": len(self._candles_m1),
            "initialized": self._initialized,
        }

    def get_current_conditions(self) -> dict[str, Any]:
        if not self._current_conditions:
            self._refresh_conditions()
        return self._current_conditions

    def get_decision_log(self, limit: int = 100) -> list[dict[str, Any]]:
        return list(self._decision_log)[:limit]

    def get_live_price(self) -> dict[str, Any]:
        return {"price": self._live_price, "time": self._live_time}

    def get_status(self) -> dict[str, Any]:
        return {
            "initialized": self._initialized,
            "paused": self._paused,
            "candleCount": len(self._candles_m1),
            "livePrice": {"price": self._live_price, "time": self._live_time},
            "conditions": self.get_current_conditions(),
            "latestSignal": self._latest_signal,
            "stats": self._stats,
            "reason": self._last_reason,
        }
